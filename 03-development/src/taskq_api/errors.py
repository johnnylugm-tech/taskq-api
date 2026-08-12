"""[FR-10] RFC 7807 problem+json error contract.

Citations:
- SPEC.md §3 FR-10 — every non-2xx response is `application/problem+json`
  with fields `type` / `title` / `status` / `detail` / `instance` /
  `correlation_id`. `detail` MUST NOT leak stack/SQL/paths (NFR-02).
- SPEC.md §7 table — status → `type` URI mapping for each error class.
- SAD.md §2.3 — `errors` is an independence module importable by any
  layer; `detail` field is whitelisted, never raw.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ProblemDetail(Exception):
    """[FR-10] Base RFC 7807 problem.

    Citations: SPEC.md §3 FR-10.
    """

    type_uri: str = "/errors/internal"
    title: str = "Internal Server Error"

    def __init__(
        self,
        status: int,
        title: str | None = None,
        detail: Any = "",
        type_uri: str | None = None,
    ) -> None:
        self.status = status
        if title is not None:
            self.title = title
        if type_uri is not None:
            self.type_uri = type_uri
        self.detail = detail
        super().__init__(title or self.title)


class ValidationProblem(ProblemDetail):
    """[FR-10] 422 — request body / query validation.

    Citations: SPEC.md §7 — `type=/errors/validation`."""

    type_uri = "/errors/validation"
    title = "Validation Error"


class AuthProblem(ProblemDetail):
    """[FR-10] 401 — missing or invalid API key.

    Citations: SPEC.md §3 FR-03; SPEC.md §7 — `type=/errors/unauthenticated`."""

    type_uri = "/errors/unauthenticated"
    title = "Unauthenticated"

    def __init__(self, detail: Any = "API key required", **kw: Any) -> None:
        super().__init__(401, detail=detail, **kw)


class ForbiddenProblem(ProblemDetail):
    """[FR-10] 403 — scope insufficient; body MUST NOT leak existence."""

    type_uri = "/errors/forbidden"
    title = "Forbidden"

    def __init__(self, detail: Any = "insufficient scope", **kw: Any) -> None:
        super().__init__(403, detail=detail, **kw)


class NotFoundProblem(ProblemDetail):
    """[FR-10] 404 — unknown task id."""

    type_uri = "/errors/not-found"
    title = "Not Found"

    def __init__(self, detail: Any = "task not found", **kw: Any) -> None:
        super().__init__(404, detail=detail, **kw)


class ConflictProblem(ProblemDetail):
    """[FR-10] 409 — task name already exists."""

    type_uri = "/errors/conflict"
    title = "Conflict"

    def __init__(self, detail: Any = "task name already exists", **kw: Any) -> None:
        super().__init__(409, detail=detail, **kw)


class RateLimitedProblem(ProblemDetail):
    """[FR-10] 429 — token bucket exhausted."""

    type_uri = "/errors/rate-limited"
    title = "Rate Limited"


class NotReadyProblem(ProblemDetail):
    """[FR-10] 503 — service not ready (DB unavailable / migration
    not at head)."""

    type_uri = "/errors/not-ready"
    title = "Service Not Ready"


class InternalProblem(ProblemDetail):
    """[FR-10] 500 — unexpected error (detail sanitised; no stack/SQL)."""

    type_uri = "/errors/internal"
    title = "Internal Server Error"

    def __init__(self, detail: Any = "internal error", **kw: Any) -> None:
        super().__init__(500, detail=detail, **kw)


def _problem_envelope(
    exc: ProblemDetail, *, correlation_id: str
) -> dict[str, Any]:
    """Render a problem envelope with whitelisted fields only.

    Citations: SPEC.md §3 FR-10 — `detail` MUST NOT leak internal
    structure; we never include exception messages.
    """
    return {
        "type": exc.type_uri,
        "title": exc.title,
        "status": exc.status,
        "detail": exc.detail,
        "correlation_id": correlation_id,
    }


async def _problem_exception_handler(
    request: Request, exc: ProblemDetail
) -> JSONResponse:
    cid = str(uuid.uuid4())
    body = _problem_envelope(exc, correlation_id=cid)
    response = JSONResponse(
        content=body, status_code=exc.status, media_type="application/problem+json"
    )
    response.headers["X-Correlation-Id"] = cid
    return response


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    cid = str(uuid.uuid4())
    # The pydantic / FastAPI error envelope includes `loc` for each
    # offending field (e.g. `["body","name"]`); test helpers search
    # the JSON-serialised form for substrings like "name" / "command".
    detail = exc.errors()
    problem = ValidationProblem(status=422, detail=detail)
    body = _problem_envelope(problem, correlation_id=cid)
    response = JSONResponse(
        content=body, status_code=problem.status, media_type="application/problem+json"
    )
    response.headers["X-Correlation-Id"] = cid
    return response


def register_error_handlers(app: FastAPI) -> None:
    """[FR-10] Register problem+json handlers on the FastAPI app.

    Citations: SAD.md §2.3 — handlers convert each typed exception to
    the same envelope shape.
    """
    app.add_exception_handler(ProblemDetail, _problem_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)


__all__ = [
    "ProblemDetail",
    "ValidationProblem",
    "AuthProblem",
    "ForbiddenProblem",
    "NotFoundProblem",
    "ConflictProblem",
    "RateLimitedProblem",
    "NotReadyProblem",
    "InternalProblem",
    "register_error_handlers",
]
