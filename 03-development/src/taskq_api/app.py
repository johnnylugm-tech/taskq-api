"""TaskQ ASGI application.

[FR-01]
Citations: SPEC.md lines 79-91, 339.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError

from taskq_api.api.tasks import router as tasks_router
from taskq_api.errors import Problem, problem_handler, validation_handler
from taskq_api.repository.task_repo import TaskRepository
from taskq_api.service.tasks import TaskService
from taskq_api.transport import install_sync_asgi_transport


def create_app() -> FastAPI:
    """Build the task resource API. [FR-01]

    Citations: SPEC.md lines 79-91, 339.
    """
    application = FastAPI(title="TaskQ API")
    application.state.task_service = TaskService(TaskRepository())
    application.include_router(tasks_router)
    application.add_exception_handler(Problem, problem_handler)  # type: ignore[arg-type]
    application.add_exception_handler(
        RequestValidationError, validation_handler  # type: ignore[arg-type]
    )

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


install_sync_asgi_transport()