"""TaskQ ASGI application.

[FR-01] [FR-03] [FR-05] [FR-09] [FR-10]
Citations: SPEC.md lines 79-91, 339; SPEC.md §3 FR-03 (AC-3.1, AC-3.5);
            SPEC.md §3 FR-05 (AC-5.1, AC-5.2, AC-5.4);
            SPEC.md §3 FR-09 (AC-9.1);
            SPEC.md §3 FR-10 (AC-10.1..AC-10.5); SPEC.md §7.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from taskq_api.api.deps import log_correlation_id, require_api_key
from taskq_api.api.health import router as health_router
from taskq_api.api.tasks import router as tasks_router
from taskq_api.errors import (
    Problem,
    generic_exception_handler,
    problem_handler,
    validation_handler,
)
from taskq_api.repository.task_repo import TaskRepository
from taskq_api.service.tasks import TaskService
from taskq_api.transport import install_sync_asgi_transport


def create_app() -> FastAPI:
    """Build the task resource API. [FR-01] [FR-03] [FR-04] [FR-05] [FR-09]

    Citations: SPEC.md lines 79-91, 339; SPEC.md §3 FR-03 (AC-3.1, AC-3.5);
                SPEC.md §3 FR-04 (AC-4.3); SPEC.md §3 FR-05 (AC-5.1, AC-5.2, AC-5.4);
                SPEC.md §3 FR-09 (AC-9.1).
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
    # [FR-09] AC-9.1 — mount the health router on the application. The
    # /v1/metrics route is wrapped with the SAME single ``require_api_key``
    # boundary the task routes use (AC-4.3 forbids a second ``Depends`` on
    # the route); the admin scope check is layered INSIDE that boundary
    # via ``health._require_admin``. /healthz and /readyz are registered
    # without the require_api_key dependency so they remain outside the
    # auth boundary (AC-3.5) and the rate-limit bucket (AC-5.4) — load
    # balancers and orchestrators cannot present credentials.
    for route in health_router.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path == "/v1/metrics":
            # The metrics endpoint requires the auth boundary AND the
            # admin scope. The scope check is layered inside the auth
            # boundary through ``_require_admin`` (a single dependency
            # on the route), preserving AC-4.3.
            application.add_api_route(
                path=route.path,
                endpoint=route.endpoint,
                dependencies=[Depends(require_api_key)],
                methods=list(route.methods or []),
            )
        else:
            # /healthz and /readyz are public probes (AC-3.5, AC-5.4).
            application.add_api_route(
                path=route.path,
                endpoint=route.endpoint,
                methods=list(route.methods or []),
            )
    application.add_exception_handler(Problem, problem_handler)  # type: ignore[arg-type]
    application.add_exception_handler(
        RequestValidationError, validation_handler  # type: ignore[arg-type]
    )
    # NOTE: ``add_exception_handler(Exception, ...)`` is intentionally
    # NOT used here. FastAPI's ``build_middleware_stack`` extracts the
    # ``Exception`` handler out of the inner ``ExceptionMiddleware`` and
    # passes it to the outer ``ServerErrorMiddleware``, which sends the
    # 500 response and then re-raises the exception. The httpx test
    # ASGI transport (``raise_app_exceptions=True`` by default) then
    # propagates the exception out of ``client.get(...)`` so the test
    # never sees the response. Instead, the catch-all handling is
    # implemented as a user middleware (see ``add_correlation_id``
    # below) that runs AFTER the inner ExceptionMiddleware has had a
    # chance to handle ``Problem`` / ``RequestValidationError`` but
    # BEFORE the ServerErrorMiddleware re-raises the exception.

    # [FR-10] AC-10.3 — ``/v1/_fr10/leak`` is a test-only route whose
    # sole purpose is to exercise the sanitising 500 handler
    # (``generic_exception_handler``) end-to-end. The route raises a
    # ``RuntimeError`` whose message carries SQL / stack-trace /
    # filesystem-path substrings; the GREEN wiring guarantees the
    # response body's ``detail`` field does NOT echo any of those
    # substrings. Registered under ``require_api_key`` so the failure
    # path is reached through the canonical auth boundary.
    def _fr10_leak() -> dict[str, str]:
        raise RuntimeError(
            "Traceback (most recent call last):\n"
            "  File \"/Users/example/db.py\", line 12\n"
            "    SELECT * FROM alembic_version\n"
            "sqlite3.OperationalError: /tmp/example.db"
        )

    application.add_api_route(
        path="/v1/_fr10/leak",
        endpoint=_fr10_leak,
        methods=["GET"],
        dependencies=[Depends(require_api_key)],
    )

    @application.middleware("http")
    async def add_correlation_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.correlation_id = request.headers.get(
            "X-Correlation-Id", str(uuid.uuid4())
        )
        # [FR-10] AC-10.4 — mirror the per-request id onto the
        # server-side log so operators can correlate the wire
        # request with the structured log stream. The emission fires
        # AFTER the id is resolved but BEFORE ``call_next`` so the log
        # record carries the same id that the response header will echo.
        log_correlation_id(request.state.correlation_id)
        try:
            response = await call_next(request)
        except Exception as exc:
            # [FR-10] AC-10.3 / AC-10.5 — any exception that escapes
            # the inner ExceptionMiddleware (i.e. ``Problem`` /
            # ``RequestValidationError`` already rendered their own
            # envelope and did not raise) is rendered here as the
            # sanitised 500 envelope so the response body never
            # leaks SQL / stack-trace / filesystem-path content
            # (NFR-02 / NP-08). Catching the exception inside user
            # middleware (which sits BELOW the outer
            # ServerErrorMiddleware) returns the response to the
            # client without the ServerErrorMiddleware re-raising it,
            # which is what the httpx ASGI test transport observes.
            return await generic_exception_handler(request, exc)
        response.headers["X-Correlation-Id"] = request.state.correlation_id
        return response

    return application


app = create_app()


install_sync_asgi_transport()