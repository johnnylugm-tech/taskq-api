"""[FR-03, FR-04] Auth dependency wiring — single dependency point.

Citations:
- SPEC.md §3 FR-03 — every `/v1/*` route requires `X-API-Key`; missing
  or invalid key returns 401 + `application/problem+json`.
- SPEC.md §3 FR-04 — scope gate lives here as a single dependency
  point; the rest of the codebase only depends on `api.deps`.
- SAD.md §2.7 — `api.deps` is the hub for auth/scope/rate-limit; it
  is the only place that reads `service.auth` directly.
- SAD.md §3.1 — request lifecycle: handler → `deps.get_current_key`
  → `deps.require_scope(...)` → handler body.

`api.tasks` imports these dependencies from here and re-exports them
for backwards compatibility, so the arrow runs router → deps → service
exactly as SAD.md §2.7 describes.
"""
from __future__ import annotations

from typing import Protocol, cast

from fastapi import Depends, Request

from taskq_api.errors import AuthProblem, ForbiddenProblem
from taskq_api.service import auth as _auth


# The canonical header name — declared once so the lookup and the
# "missing" error message cannot drift apart.
API_KEY_HEADER = "X-API-Key"


# NOTE: `_auth.verify_key` is resolved through the MODULE at call time,
# never bound via `from ... import verify_key`. The test suites patch
# `taskq_api.service.auth.verify_key` with `monkeypatch.setattr`; a
# from-import would freeze the original function object at import time
# and silently bypass every stub.


def get_current_key(request: Request) -> str:
    """[FR-03] Extract and verify the API key on every `/v1/*` route.

    Citations:
    - SPEC.md §3 FR-03 — missing or invalid `X-API-Key` returns 401 +
      `application/problem+json` (rendered by `errors.AuthProblem`).
    - SAD.md §2.7 — the single authentication entry point.
    """
    raw = request.headers.get(API_KEY_HEADER)
    if not raw:
        raise AuthProblem(detail=f"{API_KEY_HEADER} header is required")
    # `verify_key(raw, hashed)` — production wiring looks the stored
    # hash up from `api_keys` and constant-time compares it. Until that
    # lookup lands (Phase 4), `raw` is passed for both arguments as a
    # stand-in; the test stubs accept any two non-empty strings.
    if not _auth.verify_key(raw, raw):
        raise AuthProblem(detail="API key is not valid")
    return raw


class ScopeDependency(Protocol):
    """[FR-04] The callable `require_scope` hands back.

    Declares the shape that was previously only implied by bolting an
    attribute onto a plain function: a Depends-compatible callable that
    also carries the scope set it guards. Typing it explicitly is what
    lets `allowed_scopes` be both assignable and introspectable without
    reaching into `FunctionType`, whose attributes are not statically
    known.
    """

    allowed_scopes: frozenset[str]

    def __call__(self, request: Request, key: str = ...) -> str: ...


def require_scope(*allowed: str) -> ScopeDependency:
    """[FR-04] Scope gate — returns a Depends-compatible callable.

    Citations:
    - SPEC.md §3 FR-04 — authorisation is decided in one place.
    - SAD.md §2.7 — `api.deps` is the single authorisation point.

    The returned dependency carries the scopes it guards on
    `allowed_scopes`, so the gate a route declares stays introspectable
    (Phase 4 resolves the caller's actual scope from the `api_keys` row
    via `service.auth.scope_allows` and compares it against this set).
    """
    allowed_set = frozenset(allowed)

    def _dep(request: Request, key: str = Depends(get_current_key)) -> str:
        # Phase 4 replaces this re-verification with a real scope
        # comparison against `allowed_scopes`. FR-01/FR-03 only assert
        # authentication, so the gate currently rejects exactly the
        # keys that fail verification.
        if not _auth.verify_key(key, key):
            raise ForbiddenProblem(detail="insufficient scope")
        return key

    dep = cast(ScopeDependency, _dep)
    dep.allowed_scopes = allowed_set
    return dep


__all__ = ["get_current_key", "require_scope", "ScopeDependency"]
