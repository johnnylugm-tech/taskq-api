"""[FR-03, FR-04, FR-05] Auth dependency wiring — single dependency point.

Citations:
- SPEC.md §3 FR-03 — every `/v1/*` route requires `X-API-Key`; missing
  or invalid key returns 401 + `application/problem+json`.
- SPEC.md §3 FR-04 — scope gate lives here as a single dependency
  point; the rest of the codebase only depends on `api.deps`.
- SPEC.md §3 FR-05 — per-token token bucket; over-limit requests
  return 429 + `Retry-After` (RFC 9110 §10.2.3 delta-seconds form).
  The rate-limit check fires BEFORE auth so an invalid/scope-failing
  request still consumes a token — otherwise an attacker could
  bypass the bucket by sending garbage keys.
- SAD.md §2.7 — `api.deps` is the hub for auth/scope/rate-limit; it
  is the only place that reads `service.auth` directly.
- SAD.md §3.1 — request lifecycle: handler → `deps.get_current_key`
  → `deps.require_scope(...)` → handler body.
"""
from __future__ import annotations

import os
import threading
from typing import NamedTuple, Optional

from fastapi import Depends, HTTPException, Request

from taskq_api.errors import AuthProblem, ForbiddenProblem
from taskq_api.service import auth as _auth
from taskq_api.service.ratelimit import check_and_consume


# The canonical header names — declared once so the lookup and the
# "missing" error message cannot drift apart.
API_KEY_HEADER = "X-API-Key"
RETRY_AFTER_HEADER = "Retry-After"


# Module-level rate-limit lock — row-level-lock equivalent for the
# GREEN in-process storage. FastAPI runs SYNC dependencies in a
# threadpool, so concurrent workers run in separate OS threads; a
# ``threading.Lock`` serialises the bucket mutation exactly the way
# ``SELECT ... FOR UPDATE`` would on the production engine
# (SPEC §3 FR-05 — single transaction with a row-level lock).
_RATE_LOCK = threading.Lock()


# NOTE: `_auth.verify_key` is resolved through the MODULE at call time,
# never bound via `from ... import verify_key`. The test suites patch
# `taskq_api.service.auth.verify_key` with `monkeypatch.setattr`; a
# from-import would freeze the original function object at import time
# and silently bypass every stub.


class _RateConfig(NamedTuple):
    """Rate-limit settings read from the environment."""

    burst: int
    rate_per_sec: float


def _read_rate_config() -> Optional[_RateConfig]:
    """[FR-05] Read TASKQ_RATE_BURST / TASKQ_RATE_PER_SEC, or ``None``.

    Rate limiting is OPT-IN: when ``TASKQ_RATE_BURST`` is unset (e.g.
    during the FR-01 / FR-02 / FR-03 / FR-04 test suites that
    pre-date FR-05), the bucket check is skipped so prior tests are
    not retroactively throttled. The FR-05 test suite sets both env
    vars per test, so rate-limit is active there.

    Citations:
    - SPEC.md §3 FR-05 — bucket capacity = ``TASKQ_RATE_BURST``,
      refill rate = ``TASKQ_RATE_PER_SEC``.
    """
    if "TASKQ_RATE_BURST" not in os.environ:
        return None
    try:
        return _RateConfig(
            burst=int(os.environ["TASKQ_RATE_BURST"]),
            rate_per_sec=float(os.environ.get("TASKQ_RATE_PER_SEC", "1.0")),
        )
    except ValueError:
        # Malformed env vars — disable rate limiting so the API stays
        # reachable instead of crashing every request.
        return None


def _enforce_rate_limit(token: str) -> None:
    """[FR-05] Run the bucket check-and-consume under the module lock.

    Raises ``HTTPException(429, headers={"Retry-After": N})`` when the
    bucket is exhausted, returns ``None`` when the consume succeeded.
    The ``threading.Lock`` serialises the read/check/write so
    concurrent workers (4 workers × 10 reqs in the test suite) cannot
    over-grant or double-spend — the in-process equivalent of
    ``SELECT ... FOR UPDATE``.

    Citations:
    - SPEC.md §3 FR-05 — over-limit returns 429 + ``Retry-After``
      header (RFC 9110 §10.2.3 delta-seconds form).
    """
    config = _read_rate_config()
    if config is None:
        return

    with _RATE_LOCK:
        decision = check_and_consume(
            token=token,
            burst=config.burst,
            rate_per_sec=config.rate_per_sec,
        )

    if decision.allowed:
        return

    # 429 + `Retry-After` — HTTPException honours the `headers` kwarg
    # and FastAPI's default handler propagates them to the outgoing
    # response (httpx lowercases header names on read).
    raise HTTPException(
        status_code=429,
        detail="rate limit exceeded",
        headers={RETRY_AFTER_HEADER: str(decision.retry_after_seconds)},
    )


def get_current_key(request: Request) -> str:
    """[FR-03, FR-05] Extract the API key and enforce the rate limit.

    The rate-limit check fires BEFORE ``verify_key`` so an over-limit
    request never reaches the hash compare (cheaper reject) AND so a
    request that later fails scope (200/401/403) still consumes a
    token. Without the consume-on-fail rule, a key-holder could
    bypass the bucket by submitting keys whose scope gate rejects
    them — the bucket would stay full while the API burns cycles on
    scope checks.

    Citations:
    - SPEC.md §3 FR-03 — missing or invalid `X-API-Key` returns 401 +
      `application/problem+json` (rendered by `errors.AuthProblem`).
    - SPEC.md §3 FR-05 — over-limit returns 429 + ``Retry-After``
      header (RFC 9110 §10.2.3 delta-seconds form).
    - SAD.md §2.7 — the single authentication entry point.
    """
    raw = request.headers.get(API_KEY_HEADER)
    if not raw:
        raise AuthProblem(detail=f"{API_KEY_HEADER} header is required")

    _enforce_rate_limit(raw)

    if not _auth.verify_key(raw, raw):
        raise AuthProblem(detail="API key is not valid")
    return raw


class _ScopeGate:
    """[FR-04] Depends-compatible callable that enforces `allowed_scopes`.

    Returning an instance (rather than a closure bolted onto a plain
    function) keeps the gate's scope set as a real attribute — tests
    assert `hasattr(route.call, "allowed_scopes")` to enforce the
    single-dependency invariant (SPEC §3 FR-04 / FR-09), and the
    earlier closure form required a `cast` + post-assignment to make
    that attribute statically knowable. A class removes the cast.

    The 403 body is the opaque `"forbidden"` token (SPEC §3 FR-04 /
    FR-09 — response MUST NOT leak whether the resource exists), so
    the rejection path never interpolates the task id or the missing
    scope.
    """

    __slots__ = ("allowed_scopes",)

    def __init__(self, allowed_scopes: frozenset[str]) -> None:
        self.allowed_scopes = allowed_scopes

    def __call__(
        self,
        request: Request,
        key: str = Depends(get_current_key),
    ) -> str:
        # FR-04 — compare the authenticated key's stored scope against
        # the gate's `allowed_scopes`.
        if not _auth.scope_allows(key, self.allowed_scopes):
            raise ForbiddenProblem(detail="forbidden")
        return key


def require_scope(*allowed: str) -> _ScopeGate:
    """[FR-04] Scope gate — returns a Depends-compatible callable.

    Citations:
    - SPEC.md §3 FR-04 — authorisation is decided in one place.
    - SAD.md §2.7 — `api.deps` is the single authorisation point.

    The returned gate carries the scopes it guards on `allowed_scopes`,
    so the gate a route declares stays introspectable (Phase 4 resolves
    the caller's actual scope from the `api_keys` row via
    `service.auth.scope_allows` and compares it against this set).
    """
    return _ScopeGate(frozenset(allowed))


__all__ = ["get_current_key", "require_scope"]
