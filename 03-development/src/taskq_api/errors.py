"""RFC 7807 error responses.

[FR-01] [FR-05] [FR-10]
Citations: SPEC.md lines 88-91, 387-401; SPEC.md §3 FR-05 (AC-5.2);
            SPEC.md §3 FR-10 (AC-10.1..AC-10.5); SPEC.md §7.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


_logger = logging.getLogger("taskq_api.errors")


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


# [FR-10] AC-10.3 / NFR-02 — generic 500 surface. The detail string is
# intentionally fixed (never echoes the underlying exception's message)
# so SQL / stack-trace / filesystem-path content cannot leak through
# the canonical envelope (AC-10.3, NP-08).
_INTERNAL_DETAIL = "bad-request"


class InternalProblem(Problem):
    """Represent a 500 internal error. [FR-10]

    Citations: SPEC.md §3 FR-10 (AC-10.3, AC-10.5); SPEC.md §7.

    The default ``detail`` is sanitised so it never carries SQL,
    stack-trace, or filesystem-path content from the underlying
    exception (AC-10.3 / NFR-02). ``generic_exception_handler`` below
    raises this class on any unhandled ``Exception`` so the response
    envelope is uniform across the §7 status-code table.
    """

    def __init__(self, detail: str = _INTERNAL_DETAIL) -> None:
        super().__init__(
            500,
            "Internal Server Error",
            detail,
            "/errors/internal",
        )


class NotReadyProblem(Problem):
    """Represent a 503 not-ready probe response. [FR-10]

    Citations: SPEC.md §3 FR-10 (AC-10.5); SPEC.md §7.

    Reused by ``readyz`` / health probes so the SPEC §7 503 row
    materialises as the canonical 6-field envelope. ``health._readyz``
    currently builds the envelope inline; this class exists so other
    surfaces (and tests) can raise the canonical 503 problem directly.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            503,
            "Not Ready",
            detail,
            "/errors/not-ready",
        )


def _problem_response(request: Request, problem: Problem) -> JSONResponse:
    """Build the canonical RFC 7807 ``application/problem+json`` envelope.

    The payload, status code, ``X-Correlation-Id`` header, and
    ``Retry-After`` header all flow from a single ``Problem`` instance so
    every error surface (validation / 4xx / 5xx / generic 500) shares one
    rendering path — adding a field to the envelope is a one-line change.
    """
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
    return _problem_response(request, problem)


async def validation_handler(
    request: Request, _error: RequestValidationError
) -> JSONResponse:
    """Render validation failures without echoing input. [FR-01]

    Citations: SPEC.md lines 88, 397.
    """
    problem = Problem(422, "Validation Error", "Request validation failed", "/errors/validation")
    return _problem_response(request, problem)


async def generic_exception_handler(
    request: Request, _exc: Exception
) -> JSONResponse:
    """Render any unhandled ``Exception`` as a sanitised 500. [FR-10]

    Citations: SPEC.md §3 FR-10 (AC-10.1, AC-10.3, AC-10.5); NFR-02.

    Routes through ``_problem_response`` with an ``InternalProblem`` so
    the wire body carries the same canonical RFC 7807 envelope as every
    other surface, with a fixed, sanitised ``detail`` that never echoes
    the underlying ``_exc`` message — SQL statements, stack traces,
    filesystem paths, and DB schema descriptions cannot leak to the
    caller (AC-10.3 / NFR-02 / NP-08).

    The original exception IS still emitted on the server-side log (with
    the same ``correlation_id`` that appears in the response header) so
    operators can correlate the failure with the request that triggered
    it (AC-10.4).
    """
    correlation_id = getattr(
        request.state, "correlation_id", str(uuid.uuid4())
    )
    # Server-side trace carries the original exception so operators can
    # debug; the wire body never echoes it (AC-10.3).
    _logger.exception(
        "unhandled exception",
        extra={"correlation_id": correlation_id},
    )
    return _problem_response(request, InternalProblem())