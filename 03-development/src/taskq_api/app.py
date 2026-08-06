"""TaskQ ASGI application.

[FR-01]
Citations: SPEC.md lines 79-91, 339.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import anyio
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError

from taskq_api.api.tasks import router as tasks_router
from taskq_api.errors import Problem, problem_handler, validation_handler
from taskq_api.repository.task_repo import TaskRepository
from taskq_api.service.tasks import TaskService


def create_app() -> FastAPI:
    """Build the task resource API. [FR-01]

    Citations: SPEC.md lines 79-91, 339.
    """
    application = FastAPI(title="TaskQ API")
    application.state.task_service = TaskService(TaskRepository())
    application.include_router(tasks_router)
    application.add_exception_handler(Problem, problem_handler)  # type: ignore[arg-type]
    application.add_exception_handler(RequestValidationError, validation_handler)

    @application.middleware("http")
    async def add_correlation_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.correlation_id = request.headers.get(
            "X-Correlation-Id", str(uuid.uuid4())
        )
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = request.state.correlation_id
        return response

    return application


app = create_app()


def _install_sync_asgi_transport() -> None:
    """Bridge httpx 0.28's async-only ASGITransport to a sync context manager.

    ``httpx.Client(transport=ASGITransport(app))`` requires both a synchronous
    ``handle_request`` and ``__enter__``/``__exit__`` on the transport. httpx
    0.28 only ships the async variants, so the sync acceptance harness needs
    this thin shim that delegates to ``handle_async_request`` via ``anyio.run``.
    """
    transport_cls = httpx.ASGITransport
    if hasattr(transport_cls, "handle_request"):
        return

    def _handle_request(
        transport: httpx.ASGITransport, request: httpx.Request
    ) -> httpx.Response:
        request_body = request.read()

        async def _send() -> tuple[
            int, httpx.Headers, dict[str, object], bytes
        ]:
            async_request = httpx.Request(
                request.method,
                request.url,
                headers=request.headers,
                content=request_body,
                extensions=request.extensions,
            )
            response = await transport.handle_async_request(async_request)
            body = await response.aread()
            return (
                response.status_code,
                response.headers,
                response.extensions,
                body,
            )

        status_code, headers, extensions, body = anyio.run(_send)
        return httpx.Response(
            status_code,
            headers=headers,
            content=body,
            extensions=extensions,
            request=request,
        )

    def _enter(transport: httpx.ASGITransport) -> httpx.ASGITransport:
        return transport

    def _exit(
        _transport: httpx.ASGITransport,
        _exc_type: object,
        _exc_value: object,
        _traceback: object,
    ) -> None:
        return None

    transport_cls.handle_request = _handle_request  # type: ignore[attr-defined]
    transport_cls.__enter__ = _enter  # type: ignore[attr-defined]
    transport_cls.__exit__ = _exit  # type: ignore[attr-defined]


_install_sync_asgi_transport()