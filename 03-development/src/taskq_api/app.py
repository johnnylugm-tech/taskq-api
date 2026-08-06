"""TaskQ ASGI application.

[FR-01] [FR-03] [FR-05]
Citations: SPEC.md lines 79-91, 339; SPEC.md §3 FR-03 (AC-3.1, AC-3.5);
            SPEC.md §3 FR-05 (AC-5.1, AC-5.2, AC-5.4).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from taskq_api.api.deps import require_api_key
from taskq_api.api.tasks import router as tasks_router
from taskq_api.errors import Problem, problem_handler, validation_handler
from taskq_api.repository.task_repo import TaskRepository
from taskq_api.service.tasks import TaskService
from taskq_api.transport import install_sync_asgi_transport


def create_app() -> FastAPI:
    """Build the task resource API. [FR-01] [FR-03] [FR-04] [FR-05]

    Citations: SPEC.md lines 79-91, 339; SPEC.md §3 FR-03 (AC-3.1, AC-3.5);
                SPEC.md §3 FR-04 (AC-4.3); SPEC.md §3 FR-05 (AC-5.1, AC-5.2, AC-5.4).
    """
    application = FastAPI(title="TaskQ API")
    application.state.task_service = TaskService(TaskRepository())
    # Mount the /v1/* routes under the X-API-Key + rate-limit dependency
    # chain so every task endpoint (FR-01/FR-02) participates in the
    # FR-03 auth boundary, the FR-04 single-dependency mandate (AC-4.3),
    # AND the FR-05 per-token rate-limit enforcement (AC-5.1, AC-5.2).
    # Auth and rate-limit are layered inside the SINGLE
    # ``require_api_key`` boundary (see ``api.deps``) so the route
    # declares only one dependency — AC-4.3 forbids a second
    # ``Depends(rate_limit)`` on the route. ``add_api_route`` is used
    # directly (instead of mutating ``route.dependencies`` after the
    # fact, which FastAPI does NOT honour because the route's
    # dependency tree is computed at registration time) so the
    # dependency is wired into the request lifecycle. Each
    # re-registered APIRoute ends up as a first-class entry in
    # ``application.router.routes`` so the AC-4.3 route-table inspection
    # (``app.router.routes``) finds it with a real ``path`` /
    # ``path_format``. /healthz and /readyz are registered separately
    # below so they remain reachable without credentials (AC-3.5) and
    # without the rate-limit bucket (AC-5.4).
    for route in tasks_router.routes:
        # ``tasks_router.routes`` may contain non-``APIRoute`` entries
        # (``Mount`` for sub-apps, etc.). FastAPI only exposes
        # ``path`` / ``endpoint`` / ``methods`` on ``APIRoute``; narrow
        # statically so the type checker sees the attributes and we do
        # not crash at runtime if a non-API route is ever added.
        if not isinstance(route, APIRoute):
            continue
        application.add_api_route(
            path=route.path,
            endpoint=route.endpoint,
            dependencies=[Depends(require_api_key)],
            methods=list(route.methods or []),
            status_code=getattr(route, "status_code", None) or 200,
        )
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

    # AC-3.5 — health probes bypass the X-API-Key dependency. They are
    # registered on the application (not under the /v1 router) so that
    # orchestrators and load balancers can poll them without credentials.
    @application.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness probe — returns 200 when the process is alive. [FR-03]

        Citations: SPEC.md §3 FR-03 (AC-3.5); FR-09.
        """
        return {"status": "ok"}

    @application.get("/readyz")
    def readyz() -> dict[str, str]:
        """Readiness probe — returns 200 when the service can accept traffic. [FR-03]

        Citations: SPEC.md §3 FR-03 (AC-3.5); FR-09.
        """
        return {"status": "ready"}

    return application


app = create_app()


install_sync_asgi_transport()