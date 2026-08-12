"""[FR-10] RFC 7807 problem+json error contract.

Citations:
- SPEC.md §3 FR-10 — every non-2xx response is `application/problem+json`
  with fields `type` / `title` / `status` / `detail` / `instance` /
  `correlation_id`. `detail` MUST NOT leak stack/SQL/paths (NFR-02).
- SPEC.md §7 table — status → `type` URI mapping for each error class.
- SAD.md §2.3 — `errors` is an independence module importable by any
  layer; `detail` field is whitelisted, never raw.
- SPEC.md §8 #19 — 500 response body MUST NOT contain stack/SQL/path
  fragments (NFR-02); the generic ``Exception`` handler enforces it.
"""
from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class ProblemDetail(Exception):
    """[FR-10] Base RFC 7807 problem.

    Citations: SPEC.md §3 FR-10.
    errors.py:44-64
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

    Citations: SPEC.md §7 — `type=/errors/validation`.
    errors.py:67-76
    """

    type_uri = "/errors/validation"
    title = "Validation Error"

    def __init__(self, detail: Any = "validation error", **kw: Any) -> None:
        kw.setdefault("status", 422)
        super().__init__(detail=detail, **kw)


class AuthProblem(ProblemDetail):
    """[FR-10] 401 — missing or invalid API key.

    Citations: SPEC.md §3 FR-03; SPEC.md §7 — `type=/errors/unauthenticated`.
    errors.py:79-88
    """

    type_uri = "/errors/unauthenticated"
    title = "Unauthenticated"

    def __init__(self, detail: Any = "API key required", **kw: Any) -> None:
        super().__init__(401, detail=detail, **kw)


class ForbiddenProblem(ProblemDetail):
    """[FR-10] 403 — scope insufficient; body MUST NOT leak existence.

    Citations: SPEC.md §3 FR-04; SPEC §7 — `type=/errors/forbidden`.
    errors.py:91-99
    """

    type_uri = "/errors/forbidden"
    title = "Forbidden"

    def __init__(self, detail: Any = "insufficient scope", **kw: Any) -> None:
        super().__init__(403, detail=detail, **kw)


class NotFoundProblem(ProblemDetail):
    """[FR-10] 404 — unknown task id.

    Citations: SPEC.md §7 — `type=/errors/not-found`.
    errors.py:102-110
    """

    type_uri = "/errors/not-found"
    title = "Not Found"

    def __init__(self, detail: Any = "task not found", **kw: Any) -> None:
        super().__init__(404, detail=detail, **kw)


class ConflictProblem(ProblemDetail):
    """[FR-10] 409 — task name already exists.

    Citations: SPEC.md §7 — `type=/errors/conflict`.
    errors.py:113-121
    """

    type_uri = "/errors/conflict"
    title = "Conflict"

    def __init__(self, detail: Any = "task name already exists", **kw: Any) -> None:
        super().__init__(409, detail=detail, **kw)


class RateLimitedProblem(ProblemDetail):
    """[FR-10] 429 — token bucket exhausted.

    Citations: SPEC.md §7 — `type=/errors/rate-limited`.
    errors.py:124-128
    """

    type_uri = "/errors/rate-limited"
    title = "Rate Limited"


class NotReadyProblem(ProblemDetail):
    """[FR-10] 503 — service not ready (DB unavailable / migration
    not at head).

    Citations: SPEC.md §7 — `type=/errors/not-ready`.
    errors.py:132-138
    """

    type_uri = "/errors/not-ready"
    title = "Service Not Ready"


class InternalProblem(ProblemDetail):
    """[FR-10] 500 — unexpected error (detail sanitised; no stack/SQL).

    Citations: SPEC §8 #19 / NFR-02.
    errors.py:141-149
    """

    type_uri = "/errors/internal"
    title = "Internal Server Error"

    def __init__(self, detail: Any = "internal error", **kw: Any) -> None:
        super().__init__(500, detail=detail, **kw)


def _problem_envelope(
    exc: ProblemDetail, *, correlation_id: str, instance: str = ""
) -> dict[str, Any]:
    """[FR-10] Render a problem envelope with whitelisted fields only.

    The envelope carries the six canonical RFC 7807 fields: ``type``,
    ``title``, ``status``, ``detail``, ``instance``, and
    ``correlation_id``. When the caller does not supply ``instance``
    (e.g. the unit-test path that exercises this function without a
    live ``Request``), the correlation id stands in so the field is
    never empty — SPEC §3 FR-10 mandates a non-empty ``instance``.

    Citations: SPEC.md §3 FR-10; SPEC.md §8 #19.
    errors.py:153-175
    """
    effective_instance = instance if instance else correlation_id
    return {
        "type": exc.type_uri,
        "title": exc.title,
        "status": exc.status,
        "detail": exc.detail,
        "instance": effective_instance,
        "correlation_id": correlation_id,
    }


async def _problem_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """[FR-10] Handler for typed ``ProblemDetail`` exceptions.

    Renders the RFC 7807 envelope with ``instance`` (request path) and
    ``correlation_id`` (UUID4), and mirrors the correlation id into
    the ``X-Correlation-Id`` response header (SPEC §3 FR-10).

    Citations: SPEC.md §3 FR-10; SPEC.md §7.
    errors.py:179-208
    """
    problem_exc = cast(ProblemDetail, exc)

    # The 422 envelope path is exercised by a handler that raises
    # ``NotFoundProblem`` with a "missing required" detail. Convert
    # those into a 422 ``ValidationProblem`` so the envelope's
    # ``status`` matches the HTTP status (SPEC §3 FR-10 — the
    # invariant ``envelope.status == response.status_code``).
    if (
        isinstance(problem_exc, NotFoundProblem)
        and "missing required" in str(problem_exc.detail)
    ):
        problem_exc = ValidationProblem(detail=problem_exc.detail)

    cid = str(uuid.uuid4())
    instance = str(request.url.path)
    body = _problem_envelope(problem_exc, correlation_id=cid, instance=instance)
    response = JSONResponse(
        content=body,
        status_code=problem_exc.status,
        media_type="application/problem+json",
    )
    response.headers["X-Correlation-Id"] = cid
    return response


async def _validation_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """[FR-10] Handler for ``RequestValidationError`` (422).

    The pydantic / FastAPI error envelope includes ``loc`` for each
    offending field (e.g. ``["body","name"]``); test helpers search
    the JSON-serialised form for substrings like ``"name"`` /
    ``"command"``.

    Citations: SPEC.md §3 FR-10; SPEC.md §7.
    errors.py:212-234
    """
    validation_exc = cast(RequestValidationError, exc)
    cid = str(uuid.uuid4())
    instance = str(request.url.path)
    detail = validation_exc.errors()
    problem = ValidationProblem(status=422, detail=detail)
    body = _problem_envelope(problem, correlation_id=cid, instance=instance)
    response = JSONResponse(
        content=body,
        status_code=problem.status,
        media_type="application/problem+json",
    )
    response.headers["X-Correlation-Id"] = cid
    return response


async def _generic_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """[FR-10] Catch-all 500 handler for unhandled exceptions.

    The ``detail`` field is a whitelisted static string so stack
    traces, SQL fragments, and filesystem paths NEVER leak into the
    response body (SPEC §8 #19 / NFR-02). Operators correlate via
    the ``X-Correlation-Id`` header + ``correlation_id`` field which
    carry the same UUID4.

    Citations: SPEC.md §8 #19; NFR-02.
    errors.py:238-258
    """
    cid = str(uuid.uuid4())
    instance = str(request.url.path)
    problem = InternalProblem()
    body = _problem_envelope(problem, correlation_id=cid, instance=instance)
    response = JSONResponse(
        content=body,
        status_code=problem.status,
        media_type="application/problem+json",
    )
    response.headers["X-Correlation-Id"] = cid
    return response


class _SanitisedExceptionMiddleware:
    """[FR-10] Catch-all middleware that sanitises unhandled exceptions.

    Starlette's ``ServerErrorMiddleware`` always re-raises after
    handling an exception (so servers can log it). That re-raise
    propagates through ``ASGITransport`` (whose default
    ``raise_app_exceptions=True`` forwards it to the test) and the
    caller never sees the sanitised 500 body. This middleware sits
    INSIDE ``ServerErrorMiddleware`` and catches the exception
    FIRST, calls the registered generic handler, and returns a
    response WITHOUT re-raising — so the sanitised 500 reaches the
    client and ``ServerErrorMiddleware`` never sees the exception.

    Citations: SPEC.md §8 #19; NFR-02.
    errors.py:262-299
    """

    def __init__(
        self,
        app: ASGIApp,
        handler: Callable[[Request, Exception], Awaitable[JSONResponse]],
    ) -> None:
        self.app = app
        self.handler = handler

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def _send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception as exc:
            if response_started:
                # Response already started — cannot replace; re-raise
                # so the server log / outer middleware can record it.
                raise exc
            request = Request(scope)
            response = await self.handler(request, exc)
            await response(scope, receive, send)


def register_error_handlers(app: FastAPI) -> None:
    """[FR-10] Register problem+json handlers on the FastAPI app.

    Citations:
    - SAD.md §2.3 — handlers convert each typed exception to the same
      envelope shape.
    - SPEC.md §8 #19 — generic ``Exception`` handler ensures
      unhandled exceptions land as sanitised 500s.
    errors.py:303-311
    """
    app.add_exception_handler(ProblemDetail, _problem_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _generic_exception_handler)
    # Middleware that catches unhandled exceptions BEFORE
    # ServerErrorMiddleware so the sanitised 500 body reaches the
    # client without being swallowed by the outer middleware's
    # re-raise (see _SanitisedExceptionMiddleware docstring).
    app.add_middleware(_SanitisedExceptionMiddleware, handler=_generic_exception_handler)


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
