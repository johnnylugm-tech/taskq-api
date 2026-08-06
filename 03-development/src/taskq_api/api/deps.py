"""FastAPI dependencies for request authentication.

[FR-03]
Citations: SPEC.md §3 FR-03 (AC-3.1, AC-3.5); SRS.md §3 FR-03; SAD.md §2.

The ``require_api_key`` dependency is the single boundary at which callers
prove control of a valid ``X-API-Key``. Missing or invalid credentials
raise ``Problem(401, ...)`` which the application's ``problem_handler``
renders as RFC 7807 ``application/problem+json``.

The dependency is mounted on the ``/v1/*`` router inside ``app.create_app``
so that ``/healthz`` and ``/readyz`` (AC-3.5) remain reachable without an
API key — load balancers and orchestrators cannot present credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Request

from taskq_api.errors import Problem


_MISSING_API_KEY_DETAIL = "Missing X-API-Key header"
_UNAUTHORIZED_PROBLEM_TYPE = "/errors/unauthorized"


@dataclass(frozen=True)
class ApiKeyIdentity:
    """Authenticated caller identity. [FR-03]

    Citations: SPEC.md §3 FR-03 (AC-3.1); SRS.md §3 FR-03.

    Carries the presented plaintext so downstream layers can re-verify
    against the persisted hash without re-reading the request. The
    plaintext is never logged by the framework; logging is the caller's
    responsibility.
    """

    plaintext: str
    scope: Optional[str] = None


def require_api_key(request: Request) -> ApiKeyIdentity:
    """FastAPI dependency enforcing the ``X-API-Key`` header. [FR-03]

    Citations: SPEC.md §3 FR-03 (AC-3.1); SRS.md §3 FR-03.

    Returns:
        ApiKeyIdentity: the presented key on success.

    Raises:
        Problem: ``401 Unauthorized`` (RFC 7807) when the header is absent
            or empty. Rendered by ``problem_handler`` in ``errors.py``.
    """
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise Problem(
            status=401,
            title="Unauthorized",
            detail=_MISSING_API_KEY_DETAIL,
            problem_type=_UNAUTHORIZED_PROBLEM_TYPE,
        )
    return ApiKeyIdentity(plaintext=api_key)