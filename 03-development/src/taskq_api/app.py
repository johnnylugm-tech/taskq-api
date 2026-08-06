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
    """Support HTTPX's synchronous client for the acceptance harness. [FR-01]

    Citations: TEST_SPEC.md lines 52-81.
    """
    if hasattr(httpx.ASGITransport, "handle_request"):
        return

    def handle_request(
        transport: httpx.ASGITransport, request: httpx.Request
    ) -> httpx.Response:
        if not getattr(transport, "_taskq_started", False):
            transport.app.state.task_service = TaskService(TaskRepository())
            transport._taskq_started = True

        request_body = request.read()

        async def send() -> tuple[int, httpx.Headers, dict[str, object], bytes]:
            async_request = httpx.Request(
                request.method,
                request.url,
                headers=request.headers,
                content=request_body,
                extensions=request.extensions,
            )
            response = await transport.handle_async_request(async_request)
            body = await response.aread()
            return response.status_code, response.headers, response.extensions, body

        status_code, headers, extensions, body = anyio.run(send)
        return httpx.Response(
            status_code,
            headers=headers,
            content=body,
            extensions=extensions,
            request=request,
        )

    def close(_transport: httpx.ASGITransport) -> None:
        return None

    def enter(transport: httpx.ASGITransport) -> httpx.ASGITransport:
        return transport

    def exit_transport(
        _transport: httpx.ASGITransport,
        _exc_type: object,
        _exc_value: object,
        _traceback: object,
    ) -> None:
        return None

    httpx.ASGITransport.handle_request = handle_request  # type: ignore[attr-defined]
    httpx.ASGITransport.close = close  # type: ignore[attr-defined]
    httpx.ASGITransport.__enter__ = enter  # type: ignore[attr-defined]
    httpx.ASGITransport.__exit__ = exit_transport  # type: ignore[attr-defined]


_install_sync_asgi_transport()
