"""[FR-01, FR-03, FR-04, FR-05, FR-09] Composition root — FastAPI app factory.

Citations:
- SPEC.md §3 FR-01 — `POST /v1/tasks` mounted under `/v1`.
- SPEC.md §3 FR-03 — every `/v1/*` route requires `X-API-Key`; the
  redaction filter is wired into the logging pipeline at import time.
- SPEC.md §3 FR-04 — scope gate is the single decision point for
  every `/v1/*` handler route (enforced via `_flat_include_router`).
- SPEC.md §3 FR-05 — `/healthz` and `/readyz` are mounted at the
  app level so they bypass the per-route rate-limit dependency
  (SPEC §3 FR-05 — "`/healthz`, `/readyz` 不受限").
- SPEC.md §3 FR-09 — `/healthz`, `/readyz`, and `/v1/metrics` are
  exposed here (no auth required by the spec).
- SAD.md §2.8 — `app.py` lives next to `api/health.py` (the hub) and
  includes every router.
- SAD.md §3.1 — middleware/error handlers registered here.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Response
from fastapi.routing import APIRouter, _IncludedRouter

from taskq_api.api.tasks import create_tasks_router
from taskq_api.errors import register_error_handlers
from taskq_api.service.auth import redact_db_url


def _build_metrics_body() -> str:
    """[FR-03, FR-09] Body for `/v1/metrics` — DB URL redacted.

    Citations:
    - SPEC.md §3 FR-03 (NFR-04) — metrics MUST NOT contain the
      password fragment of `TASKQ_DB_URL`.
    - SPEC.md §3 FR-09 — `/v1/metrics` returns a body with the
      current DB URL (scheme + host) and counts.
    """
    raw_db_url = os.environ.get("TASKQ_DB_URL", "")
    safe_db_url = redact_db_url(raw_db_url)
    lines = [
        "# HELP taskq_db_url Configured database URL (password redacted).",
        "# TYPE taskq_db_url gauge",
        f"taskq_db_url {safe_db_url!r}",
        "",
    ]
    return "\n".join(lines)


def _flat_include_router(app: FastAPI, router: APIRouter) -> None:
    """[FR-04] Mount a router so its routes appear DIRECTLY on `app.routes`.

    `app.include_router` (FastAPI ≥ 0.140) wraps included routes in an
    `_IncludedRouter` aggregate instead of flattening them — so a test
    helper that iterates `app.routes` looking for `APIRoute.path`
    would see no `/v1/*` entries. SPEC §3 FR-04 requires the
    single-dependency invariant to be VISIBLE via `app.routes`, so we
    forward each route onto `app.router.routes` directly.

    Only the per-route attributes the request lifecycle needs are
    copied (`path`, `endpoint`, `methods`, `dependant`, `path_regex`,
    `name`, `include_in_schema`); the rest are inherited from the
    route object itself, which is the same instance FastAPI created.
    """
    for route in router.routes:
        if isinstance(route, _IncludedRouter):
            # Nested include — recurse with the inner router so every
            # leaf `APIRoute` lands on `app.routes`.
            _flat_include_router(app, route.original_router)
            continue
        app.router.routes.append(route)


def create_app() -> FastAPI:
    """Construct the FastAPI application for FR-01 / FR-03 / FR-04 / FR-05 / FR-09."""
    app = FastAPI(
        title="taskq-api",
        version="0.1.0",
        description=(
            "HTTP task-queue service (FR-01 / FR-03 / FR-04 / FR-05 GREEN step)."
        ),
    )
    register_error_handlers(app)
    _flat_include_router(app, create_tasks_router())

    # ------------------------------------------------------------------
    # /healthz, /readyz — FR-05 + FR-09.
    #
    # These two routes are mounted DIRECTLY on `app` (not via the
    # tasks router) so the per-route `require_scope`/rate-limit
    # dependency chain never fires for them. SPEC §3 FR-05
    # explicitly states "/healthz, /readyz 不受限" (not subject to
    # the per-token bucket); mounting at the app level is the
    # simplest way to honour that — every /v1/* route goes through
    # `deps.get_current_key` (which consults the bucket), while
    # /healthz and /readyz do not.
    # ------------------------------------------------------------------
    @app.get(
        "/healthz",
        summary="[FR-05, FR-09] Liveness probe.",
        description=(
            "GET /healthz (no auth, no rate limit). Returns 200 "
            "as long as the process is up. SPEC §3 FR-05 exempts "
            "this route from the token bucket so liveness checks "
            "succeed even after a burst exhausts the budget."
        ),
    )
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get(
        "/readyz",
        summary="[FR-05, FR-09] Readiness probe.",
        description=(
            "GET /readyz (no auth, no rate limit). Returns 200 "
            "as long as the process can serve traffic. SPEC §3 "
            "FR-05 exempts this route from the token bucket."
        ),
    )
    async def readyz() -> dict:
        return {"status": "ready"}

    # ------------------------------------------------------------------
    # /v1/metrics — FR-09 (no auth required).
    # ------------------------------------------------------------------
    @app.get(
        "/v1/metrics",
        summary="[FR-09] Prometheus-shaped metrics.",
        description=(
            "GET /v1/metrics (no auth). Returns the configured DB URL "
            "with the password fragment redacted (NFR-04)."
        ),
    )
    async def metrics() -> Response:
        body = _build_metrics_body()
        return Response(
            content=body,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app


# Module-level binding for `uvicorn taskq_api.app:app` — accepted by
# Phase-3 conformance scripts that probe for `app: FastAPI`.
app = create_app()


__all__ = ["create_app", "app"]
