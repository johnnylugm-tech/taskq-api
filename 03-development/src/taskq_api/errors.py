"""RFC 7807 error responses.

[FR-01] [FR-05]
Citations: SPEC.md lines 88-91, 387-401; SPEC.md §3 FR-05 (AC-5.2).
"""

from __future__ import annotations

import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class Problem(Exception):
    """Represent an application/problem+json failure. [FR-01] [FR-05]

    Citations: SPEC.md lines 88-91, 387-401; SPEC.md §3 FR-05 (AC-5.2).

    The ``retry_after`` attribute is set on rate-limit Problems (AC-5.2)
    so the JSON response carries a ``Retry-After`` header (RFC 7231
    §7.1.3) with the number of seconds the caller should wait before
    retrying. Non-rate-limit problems leave it as ``None``.
    """

    def __init__(
        self,
        status: int,
        title: str,
        detail: str,
        problem_type: str,
        *,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.title = title
        self.detail = detail
        self.problem_type = problem_type
        self.retry_after = retry_after


class NotFoundProblem(Problem):
    """Represent an unknown task resource. [FR-01]

    Citations: SPEC.md lines 89, 393.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(404, "Not Found", detail, "/errors/not-found")


class ConflictProblem(Problem):
    """Represent a task-name uniqueness conflict. [FR-01]

    Citations: SPEC.md lines 88, 395.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(409, "Conflict", detail, "/errors/conflict")


class RateLimitedProblem(Problem):
    """Represent a per-token rate-limit miss. [FR-05]

    Citations: SPEC.md §3 FR-05 (AC-5.2).
    """

    def __init__(self, detail: str, retry_after: float) -> None:
        super().__init__(
            429,
            "Too Many Requests",
            detail,
            "/errors/rate-limited",
            retry_after=retry_after,
        )


def _response(request: Request, problem: Problem) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
    payload = {
        "type": problem.problem_type,
        "title": problem.title,
        "status": problem.status,
        "detail": problem.detail,
        "instance": request.url.path,
        "correlation_id": correlation_id,
    }
    headers = {"X-Correlation-Id": correlation_id}
    # [FR-05] AC-5.2 — surface ``Retry-After`` so a 429 caller can
    # back off without re-deriving the bucket refill math.
    retry_after = getattr(problem, "retry_after", None)
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        payload,
        status_code=problem.status,
        headers=headers,
        media_type="application/problem+json",
    )


async def problem_handler(request: Request, problem: Problem) -> JSONResponse:
    """Render an application failure without leaking internals. [FR-01]

    Citations: SPEC.md lines 88-91, 387-401.
    """
    return _response(request, problem)


async def validation_handler(
    request: Request, _error: RequestValidationError
) -> JSONResponse:
    """Render validation failures without echoing input. [FR-01]

    Citations: SPEC.md lines 88, 397.
    """
    problem = Problem(422, "Validation Error", "Request validation failed", "/errors/validation")
    return _response(request, problem)
