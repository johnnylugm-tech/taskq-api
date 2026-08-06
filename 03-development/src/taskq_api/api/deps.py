"""FastAPI dependencies for request authentication and authorisation.

[FR-03] [FR-04] [FR-05]
Citations: SPEC.md §3 FR-03 (AC-3.1, AC-3.5); SPEC.md §3 FR-04 (AC-4.1..AC-4.3);
            SPEC.md §3 FR-05 (AC-5.1, AC-5.2, AC-5.4);
            SRS.md §3 FR-03; SRS.md §3 FR-04; SRS.md §3 FR-05; SAD.md §2.

The ``require_api_key`` dependency is the single boundary at which callers
prove control of a valid ``X-API-Key``. Missing or invalid credentials
raise ``Problem(401, ...)`` which the application's ``problem_handler``
renders as RFC 7807 ``application/problem+json``.

The ``require_scope`` factory builds per-route authorisation dependencies
that consume the ``ApiKeyIdentity`` produced by ``require_api_key`` and
enforce the strict inclusive ``read < write < admin`` hierarchy defined
in ``service.auth.SCOPE_HIERARCHY``. An insufficient scope raises
``Problem(403, ...)`` whose body never echoes the probed resource id
(AC-4.2 / NP-02).

Per AC-4.3 there is exactly ONE dependency on every ``/v1/*`` route,
and that dependency (``require_api_key``) is also the place where the
per-token rate-limit bucket is consumed (AC-5.1, AC-5.2). The
``/healthz`` and ``/readyz`` probes (AC-3.5, AC-5.4) are registered
separately on the application — they bypass ``require_api_key`` and
therefore bypass the rate-limit boundary entirely. Load balancers and
orchestrators cannot present credentials and MUST NOT be gated by the
per-token bucket.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from fastapi import Request

from taskq_api.errors import Problem, RateLimitedProblem
from taskq_api.service.auth import scope_satisfies
from taskq_api.service.ratelimit import TokenBucket, _default_bucket, record_rejection


_MISSING_API_KEY_DETAIL = "Missing X-API-Key header"
_UNAUTHORIZED_PROBLEM_TYPE = "/errors/unauthorized"

# [FR-04] AC-4.2 — stable problem+json ``type`` URI for the 403 path.
# The detail string is intentionally generic so that an attacker probing
# the API cannot enumerate resources by reading the 403 body (NP-02).
SCOPE_FORBIDDEN_PROBLEM_TYPE = "/errors/forbidden"
_SCOPE_FORBIDDEN_DETAIL = "Insufficient scope"

# [FR-05] AC-5.2 — stable problem+json ``type`` URI for the 429 path.
# The detail string is intentionally generic so the failure does not
# double as a low-cost discovery oracle for the bucket's remaining
# tokens. The constant stays in sync with ``RateLimitedProblem``'s
# hardcoded URI in ``errors.py`` — both surfaces document the same
# contract (AC-5.2).
RATE_LIMITED_PROBLEM_TYPE = "/errors/rate-limited"
_RATE_LIMITED_DETAIL = "Rate limit exceeded"


@dataclass(frozen=True)
class ApiKeyIdentity:
    """Authenticated caller identity. [FR-03] [FR-04]

    Citations: SPEC.md §3 FR-03 (AC-3.1); SPEC.md §3 FR-04 (AC-4.2).

    Carries the presented plaintext so downstream layers can re-verify
    against the persisted hash without re-reading the request. The
    plaintext is never logged by the framework; logging is the caller's
    responsibility. The ``scope`` is the privilege rank looked up from
    the registered key store; ``None`` means no scope is associated.
    """

    plaintext: str
    scope: Optional[str] = None


# Module-level registry of API keys mapped to their scope. Populated by
# ``register_key`` (e.g. from ``create_api_key`` in ``service.auth``).
# [FR-04] AC-4.2 — unknown / unregistered keys surface as 403 so the
# caller cannot distinguish "unknown key" from "insufficient scope",
# which is the anti-enumeration guarantee the NP-02 threat model demands.
_KEY_SCOPES: dict[str, str] = {}

# [FR-05] Set of plaintexts that are exempt from the per-token rate-limit
# bucket. Populated by ``register_key(..., rate_limit_bypass=True)`` and
# consulted by ``rate_limit_dependency`` before consuming a token. Used
# by the test conftest to mark fixture keys (which make many requests
# in a single test, e.g. the FR-01 pagination suite) as exempt so the
# burst capacity does not bleed into the prior FR acceptance tests.
# Production callers leave the default ``False`` so every key is
# gated by the bucket.
_NO_RATE_LIMIT_KEYS: set[str] = set()


def register_key(
    plaintext: str,
    scope: str,
    *,
    rate_limit_bypass: bool = False,
) -> None:
    """Record ``plaintext`` as a known key with the given scope. [FR-04] [FR-05]

    Citations: SPEC.md §3 FR-04 (AC-4.2); SPEC.md §3 FR-05 (AC-5.1);
                SRS.md §3 FR-04; SRS.md §3 FR-05.

    Used by the key-creation flow to publish a freshly minted plaintext
    into the in-process registry that ``require_api_key`` consults.

    When ``rate_limit_bypass`` is True the key is added to the
    rate-limit bypass set so the per-token bucket does not consume
    tokens for requests authenticated with this key. Intended for
    test fixture keys; production code should leave it False.
    """
    _KEY_SCOPES[plaintext] = scope
    if rate_limit_bypass:
        _NO_RATE_LIMIT_KEYS.add(plaintext)


def require_api_key(request: Request) -> ApiKeyIdentity:
    """FastAPI dependency enforcing the ``X-API-Key`` header and per-token rate limit. [FR-03] [FR-04] [FR-05]

    Citations: SPEC.md §3 FR-03 (AC-3.1); SPEC.md §3 FR-04 (AC-4.2, AC-4.3);
                SPEC.md §3 FR-05 (AC-5.1, AC-5.2);
                SRS.md §3 FR-03; SRS.md §3 FR-04; SRS.md §3 FR-05.

    Auth (AC-3.1) and per-token rate limit (AC-5.1, AC-5.2) are applied
    at the same boundary so the /v1/* routes need only this single
    dependency (AC-4.3 — "ONE auth dependency covers all v1 routes").
    The rate-limit consume runs AFTER auth so the resolved identity is
    available for bucket lookup.

    Returns:
        ApiKeyIdentity: the presented key on success.

    Raises:
        Problem: ``401 Unauthorized`` (RFC 7807) when the header is absent
            or empty. Rendered by ``problem_handler`` in ``errors.py``.
        Problem: ``403 Forbidden`` (RFC 7807) when the presented key is
            not registered. The body never echoes the requested path or
            any path parameter so an attacker cannot enumerate resources
            (AC-4.2 / NP-02).
        RateLimitedProblem: ``429 Too Many Requests`` (RFC 7807) when
            the per-token bucket is empty. The ``retry_after`` value is
            the time until the next token is available; the response
            handler surfaces it as the ``Retry-After`` header.
    """
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise Problem(
            status=401,
            title="Unauthorized",
            detail=_MISSING_API_KEY_DETAIL,
            problem_type=_UNAUTHORIZED_PROBLEM_TYPE,
        )
    scope = _KEY_SCOPES.get(api_key)
    if scope is None:
        # Anti-enumeration: an unregistered key looks identical to a
        # key whose privilege is too low. The body is canonical and
        # carries no information about the requested resource.
        raise Problem(
            status=403,
            title="Forbidden",
            detail=_SCOPE_FORBIDDEN_DETAIL,
            problem_type=SCOPE_FORBIDDEN_PROBLEM_TYPE,
        )
    identity = ApiKeyIdentity(plaintext=api_key, scope=scope)
    # [FR-05] AC-5.1, AC-5.2 — apply the per-token rate limit at the
    # auth boundary so the /v1/* routes only need a single dependency
    # (AC-4.3). Identity is fully resolved at this point, so the bucket
    # can be looked up by plaintext.
    rate_limit_dependency(identity)
    return identity


def require_scope(scope: str) -> Callable[[ApiKeyIdentity], None]:
    """FastAPI dependency factory enforcing a minimum scope. [FR-04]

    Citations: SPEC.md §3 FR-04 (AC-4.2, AC-4.3); SRS.md §3 FR-04.

    Returns a dependency that reads the ``ApiKeyIdentity`` resolved by
    ``require_api_key`` and raises ``Problem(403, ...)`` when the
    identity's scope does not satisfy the required ``scope`` per
    ``service.auth.scope_satisfies``.

    The 403 body is the canonical RFC 7807 problem document; its
    ``detail`` and ``type`` fields never echo the URL path or any
    resource id the caller probed (NP-02 / AC-4.2).
    """

    def _dep(identity: ApiKeyIdentity) -> None:
        # ``require_api_key`` rejects keys with no registered scope, so by
        # the time this dependency runs ``identity.scope`` is guaranteed to
        # be a str. Narrow explicitly for the type checker.
        token_scope: str = identity.scope or ""
        if not scope_satisfies(token_scope, scope):
            raise Problem(
                status=403,
                title="Forbidden",
                detail=_SCOPE_FORBIDDEN_DETAIL,
                problem_type=SCOPE_FORBIDDEN_PROBLEM_TYPE,
            )
        return None

    return _dep


# ---------------------------------------------------------------------------
# FR-05 — per-token rate limiting
# ---------------------------------------------------------------------------

# Per-key bucket registry. Lazily populated on first request so the
# ``register_key`` flow does not have to know about the rate-limit
# subsystem. The dict is keyed by the presented plaintext so the
# ``ApiKeyIdentity`` returned by ``require_api_key`` (which carries the
# plaintext) resolves to the same bucket across requests.
_KEY_BUCKETS: dict[str, TokenBucket] = {}


def _bucket_for_key(plaintext: str) -> TokenBucket:
    """Return the bucket associated with ``plaintext``, creating one on demand. [FR-05]

    Citations: SPEC.md §3 FR-05 (AC-5.1, AC-5.2).
    """
    bucket = _KEY_BUCKETS.get(plaintext)
    if bucket is None:
        bucket = _default_bucket()
        _KEY_BUCKETS[plaintext] = bucket
    return bucket


def reset_buckets() -> None:
    """Forget all per-key buckets. Test hook; not called in production. [FR-05]

    Citations: SPEC.md §3 FR-05 (AC-5.1, AC-5.2).
    """
    _KEY_BUCKETS.clear()


def rate_limit_dependency(
    identity: ApiKeyIdentity,
    bucket: Optional[TokenBucket] = None,
) -> None:
    """Consume a token from the bucket associated with ``identity``. [FR-05]

    Citations: SPEC.md §3 FR-05 (AC-5.1, AC-5.2, AC-5.4); SRS.md §3 FR-05.

    The bucket is taken from the explicit ``bucket`` argument when
    supplied (the unit-test path) and otherwise resolved from the
    ``_KEY_BUCKETS`` registry keyed by the identity's plaintext (the
    end-to-end path).

    Keys in the ``_NO_RATE_LIMIT_KEYS`` set are exempt — this is used
    by the test conftest to mark fixture keys (e.g. the FR-01
    pagination suite, which makes >20 requests in a single test) as
    exempt so the burst capacity does not bleed into prior FR
    acceptance tests.

    Raises:
        Problem: ``429 Too Many Requests`` (RFC 7807) when the bucket
            cannot dispense a token. The carrying ``retry_after`` value
            is the time until the next token is available, used by the
            response handler to surface the ``Retry-After`` header.
    """
    if identity.plaintext in _NO_RATE_LIMIT_KEYS:
        return
    target = bucket if bucket is not None else _bucket_for_key(identity.plaintext)
    if not target.consume(1):
        # [FR-09] AC-9.1 — bump the running rejection counter so the
        # /v1/metrics endpoint can surface the total. The bump fires
        # before the raise so the counter is captured even when the
        # exception propagates through the exception handler.
        record_rejection()
        raise RateLimitedProblem(
            detail=_RATE_LIMITED_DETAIL,
            retry_after=target.retry_after(),
        )