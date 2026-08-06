"""FastAPI dependencies for request authentication and authorisation.

[FR-03] [FR-04]
Citations: SPEC.md §3 FR-03 (AC-3.1, AC-3.5); SPEC.md §3 FR-04 (AC-4.1..AC-4.3);
            SRS.md §3 FR-03; SRS.md §3 FR-04; SAD.md §2.

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

The dependencies are mounted on the ``/v1/*`` router inside ``app.create_app``
so that ``/healthz`` and ``/readyz`` (AC-3.5) remain reachable without an
API key — load balancers and orchestrators cannot present credentials.
AC-4.3 mandates a single auth dependency across all ``/v1/*`` routes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from fastapi import Request

from taskq_api.errors import Problem
from taskq_api.service.auth import scope_satisfies


_MISSING_API_KEY_DETAIL = "Missing X-API-Key header"
_UNAUTHORIZED_PROBLEM_TYPE = "/errors/unauthorized"

# [FR-04] AC-4.2 — stable problem+json ``type`` URI for the 403 path.
# The detail string is intentionally generic so that an attacker probing
# the API cannot enumerate resources by reading the 403 body (NP-02).
SCOPE_FORBIDDEN_PROBLEM_TYPE = "/errors/forbidden"
_SCOPE_FORBIDDEN_DETAIL = "Insufficient scope"


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


def register_key(plaintext: str, scope: str) -> None:
    """Record ``plaintext`` as a known key with the given scope. [FR-04]

    Citations: SPEC.md §3 FR-04 (AC-4.2); SRS.md §3 FR-04.

    Used by the key-creation flow to publish a freshly minted plaintext
    into the in-process registry that ``require_api_key`` consults.
    """
    _KEY_SCOPES[plaintext] = scope


def _lookup_scope(plaintext: str) -> Optional[str]:
    return _KEY_SCOPES.get(plaintext)


def require_api_key(request: Request) -> ApiKeyIdentity:
    """FastAPI dependency enforcing the ``X-API-Key`` header. [FR-03] [FR-04]

    Citations: SPEC.md §3 FR-03 (AC-3.1); SPEC.md §3 FR-04 (AC-4.2).

    Returns:
        ApiKeyIdentity: the presented key on success.

    Raises:
        Problem: ``401 Unauthorized`` (RFC 7807) when the header is absent
            or empty. Rendered by ``problem_handler`` in ``errors.py``.
        Problem: ``403 Forbidden`` (RFC 7807) when the presented key is
            not registered. The body never echoes the requested path or
            any path parameter so an attacker cannot enumerate resources
            (AC-4.2 / NP-02).
    """
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise Problem(
            status=401,
            title="Unauthorized",
            detail=_MISSING_API_KEY_DETAIL,
            problem_type=_UNAUTHORIZED_PROBLEM_TYPE,
        )
    scope = _lookup_scope(api_key)
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
    return ApiKeyIdentity(plaintext=api_key, scope=scope)


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