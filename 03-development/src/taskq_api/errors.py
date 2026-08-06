"""RFC 7807 error responses.

[FR-01]
Citations: SPEC.md lines 88-91, 387-401.
"""

from __future__ import annotations

import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class Problem(Exception):
    """Represent an application/problem+json failure. [FR-01]

    Citations: SPEC.md lines 88-91, 387-401.
    """

    def __init__(self, status: int, title: str, detail: str, problem_type: str) -> None:
        super().__init__(detail)
        self.status = status
        self.title = title
        self.detail = detail
        self.problem_type = problem_type


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
    return JSONResponse(
        payload,
        status_code=problem.status,
        headers={"X-Correlation-Id": correlation_id},
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
