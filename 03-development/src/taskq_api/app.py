"""[FR-01, FR-03, FR-04, FR-05, FR-08, FR-09] Composition root — FastAPI app factory.

Citations:
- SPEC.md §3 FR-01 — `POST /v1/tasks` mounted under `/v1`.
- SPEC.md §3 FR-03 — every `/v1/*` route requires `X-API-Key`; the
  redaction filter is wired into the logging pipeline at import time.
- SPEC.md §3 FR-04 — scope gate is the single decision point for
  every `/v1/*` handler route (enforced via `_flat_include_router`).
- SPEC.md §3 FR-05 — `/healthz` and `/readyz` are mounted at the
  app level so they bypass the per-route rate-limit dependency
  (SPEC §3 FR-05 — "`/healthz`, `/readyz` 不受限").
- SPEC.md §3 FR-08 — composition root binds the runner's
  graceful-drain shutdown contract; on ``shutdown`` the lifespan
  awaits the runner's ``shutdown(drain_timeout_seconds)`` so the
  process exits without orphan pids and the in-flight /v1/task
  run records are marked ``status='interrupted'`` (SPEC §3 FR-08).
- SPEC.md §3 FR-09 — `/healthz`, `/readyz`, and `/v1/metrics` are
  exposed here (no auth required by the spec).
- SAD.md §2.8 — `app.py` lives next to `api/health.py` (the hub) and
  includes every router.
- SAD.md §3.1 — middleware/error handlers registered here.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.routing import APIRouter, _IncludedRouter
from sqlalchemy import create_engine, text as sql_text

from taskq_api.api.tasks import create_tasks_router
from taskq_api.errors import register_error_handlers
from taskq_api.service.auth import redact_db_url
from taskq_api.service.runner import TaskRunner


# [FR-07] Head alembic revision. Compared against ``alembic_version``
# at /readyz time (SPEC §3 FR-07 / SPEC §8 #11).
_MIGRATION_HEAD: str = "v3_split_results"
_BEHIND_HEAD_PREFIX: str = "migration is behind head"


def _behind_head_detail(reason: str) -> str:
    """[FR-07] Compose the /readyz 503 detail string for a behind-head state.

    Centralises the prefix so every branch in ``_check_migration_state``
    produces a detail string with the same shape — operators can rely
    on the leading ``migration`` token regardless of which check fired.
    """
    return f"{_BEHIND_HEAD_PREFIX} ({reason})"


def _check_migration_state() -> tuple[bool, str]:
    """[FR-07] Compare alembic current revision against the configured head.

    Returns ``(is_at_head, detail_str)``. When the DB has no alembic
    metadata table, or its ``alembic_version.version_num`` does not
    match ``_MIGRATION_HEAD``, the helper reports ``(False, detail)``
    so /readyz can return 503 + ``application/problem+json`` (SPEC
    §8 #11).
    """
    db_url = os.environ.get("TASKQ_DB_URL", "")
    if not db_url:
        # No DB configured — report "behind" with a generic detail so
        # the operator sees "migration" in the response.
        return False, _behind_head_detail("no TASKQ_DB_URL configured")

    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            row = conn.execute(
                sql_text("SELECT version_num FROM alembic_version")
            ).first()
    except Exception:  # pragma: no cover — defensive
        return False, _behind_head_detail("alembic probe error")

    if row is None:
        return False, _behind_head_detail("no alembic_version row")

    current = row[0]
    if current != _MIGRATION_HEAD:
        return (
            False,
            _behind_head_detail(f"current={current}, head={_MIGRATION_HEAD}"),
        )
    return True, f"migration at head ({_MIGRATION_HEAD})"


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


def _build_lifespan() -> "AsyncContextManager[None]":
    """[FR-08] Lifespan that bound-runs the TaskRunner graceful drain.

    Citations:
    - SPEC.md §3 FR-08 — on shutdown, await the runner's
      ``shutdown(drain_timeout_seconds)`` so in-flight tasks are
      drained (or marked ``status='interrupted'``) and no orphan
      pids survive the process exit.
    - SPEC.md §3 FR-08 — ``TASKQ_DRAIN_TIMEOUT`` is the bounded
      window the composition root enforces on the runner.
    """
    drain_timeout = float(os.environ.get("TASKQ_DRAIN_TIMEOUT", "5"))

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        runner = TaskRunner()
        try:
            yield
        finally:
            # FR-08 — graceful drain; stragglers exceed the bounded
            # window get marked 'interrupted' (SPEC §3 FR-08). The
            # runner's ``shutdown`` is sync (per FR-02 contract) but
            # tests may install an async mock; handle both shapes.
            result = runner.shutdown(drain_timeout_seconds=drain_timeout)
            if asyncio.iscoroutine(result):
                await result

    return _lifespan


def create_app() -> FastAPI:
    """Construct the FastAPI application for FR-01 / FR-03 / FR-04 / FR-05 / FR-08 / FR-09."""
    app = FastAPI(
        title="taskq-api",
        version="0.1.0",
        description=(
            "HTTP task-queue service (FR-01 / FR-03 / FR-04 / FR-05 / FR-08 / FR-09 GREEN step)."
        ),
        lifespan=_build_lifespan(),
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
        summary="[FR-05, FR-07, FR-09] Readiness probe.",
        description=(
            "GET /readyz (no auth, no rate limit). Returns 200 when "
            "the process can serve traffic AND the alembic migration "
            "is at head. Returns 503 `application/problem+json` with a "
            "detail that mentions `migration` when the DB is behind "
            "head (SPEC §3 FR-05 + SPEC §8 #11)."
        ),
    )
    async def readyz():
        is_at_head, detail_str = _check_migration_state()
        if is_at_head:
            return {"status": "ready", "migration": detail_str}
        # FR-07 / SPEC §8 #11 — /readyz returns 503 with a
        # ``migration``-mentioning detail when the alembic revision is
        # behind head.
        return Response(
            content=(
                '{"type":"/errors/migration","title":"Migration Behind Head",'
                '"status":503,'
                f'"detail":"{detail_str}"'
                ',"instance":"/readyz"}'
            ),
            status_code=503,
            media_type="application/problem+json",
        )

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
