"""[FR-01, FR-03, FR-04, FR-05, FR-07, FR-08, FR-09] Composition root — FastAPI app factory.

Citations:
- SPEC.md §3 FR-01 — `POST /v1/tasks` mounted under `/v1`.
- SPEC.md §3 FR-03 — every `/v1/*` route requires `X-API-Key`; the
  redaction filter is wired into the logging pipeline at import time.
- SPEC.md §3 FR-04 — scope gate is the single decision point for
  every `/v1/*` handler route (enforced via `_flat_include_router`).
- SPEC.md §3 FR-05 — `/healthz` and `/readyz` are mounted at the
  app level so they bypass the per-route rate-limit dependency
  (SPEC §3 FR-05 — "`/healthz`, `/readyz` 不受限").
- SPEC.md §3 FR-07 — the readiness probe compares the alembic row
  against ``_MIGRATION_HEAD``; behind-head yields 503.
- SPEC.md §3 FR-08 — composition root binds the runner's
  graceful-drain shutdown contract; on ``shutdown`` the lifespan
  awaits the runner's ``shutdown(drain_timeout_seconds)`` so the
  process exits without orphan pids and the in-flight /v1/task
  run records are marked ``status='interrupted'`` (SPEC §3 FR-08).
- SPEC.md:157 §3 FR-09 — `/healthz`, `/readyz`, and `/v1/metrics` are
  exposed here (no auth required by the spec); the readiness probe
  itself lives in `taskq_api.api.health` and is mounted via the
  health router with ``_check_migration_state`` as its probe. The
  503 detail names which check failed (db vs migration).
- SAD.md §2.8 — `app.py` lives next to `api/health.py` (the hub) and
  includes every router.
- SAD.md §3.1 — middleware/error handlers registered here.
- SAD.md §3.2 — `app.py` is the only place that imports SQLAlchemy
  in the api-layer scope; the readiness probe delegates here.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Callable, Tuple, cast

from fastapi import FastAPI, Response
from fastapi.routing import APIRouter, _IncludedRouter
from sqlalchemy import create_engine, text as sql_text

from taskq_api.api.health import create_health_router
from taskq_api.api.tasks import create_tasks_router
from taskq_api.errors import register_error_handlers
from taskq_api.service.auth import redact_db_url
from taskq_api.service.runner import TaskRunner


# [FR-07] Head alembic revision. Compared against ``alembic_version``
# at /readyz time (SPEC §3 FR-07 / SPEC §8 #11).
_MIGRATION_HEAD: str = "v3_split_results"
_BEHIND_HEAD_PREFIX: str = "migration is behind head"
_UNKNOWN_PREFIX: str = "migration state unknown"


def _readyz_detail(prefix: str, reason: str) -> str:
    """[FR-07, FR-09] Format the /readyz 503 detail envelope.

    Both the "behind head" and "unknown state" branches share the same
    shape — a SPEC §8 #10/#11 grep-able prefix followed by a
    parenthetical reason naming which check failed — so a single
    formatter avoids two near-identical helpers.

    Callers pick the prefix by *what the probe learned*:

    - ``_BEHIND_HEAD_PREFIX``: the probe read ``alembic_version``
      cleanly and the revision is stale — SPEC §8 #11 wants
      ``migration`` here so the operator runs ``alembic upgrade head``.
    - ``_UNKNOWN_PREFIX``: the probe could not read state at all;
      claiming "behind head" would send an on-call operator to run
      migrations when the real fault is an unreachable database, so
      this branch says ``unknown`` and names the DB as the thing that
      failed (SPEC §8 #10).
    """
    return f"{prefix} ({reason})"


def _check_migration_state() -> Tuple[bool, str]:
    """[FR-07, FR-09] Compare alembic current revision against the configured head.

    Returns ``(is_at_head, detail_str)``. Fails closed: any state that
    is not a confirmed match for ``_MIGRATION_HEAD`` yields ``False``
    so /readyz can return 503 + ``application/problem+json``.

    The detail names WHICH check failed, per SPEC §3 FR-09 ("在 body
    說明哪一項失敗"):

    - DB unreachable / alembic metadata unreadable → ``migration state
      unknown (db ...)`` (SPEC §8 #10).
    - ``alembic_version`` read cleanly but stale/unstamped → ``migration
      is behind head (...)`` (SPEC §8 #11).

    Citations:
    - SPEC.md:157 — 503 body must state which check failed.
    - SPEC.md:160 — behind-head MUST fail closed.
    - SPEC.md:420 (§8 #10) — DB down → detail identifies the DB.
    - SPEC.md:421 (§8 #11) — behind head → detail identifies migration.
    - SPEC.md:150 (NFR-03) — ``asyncio.CancelledError`` derives from
      ``BaseException``, so the ``except Exception`` below cannot
      swallow a cancellation.

    The ``create_engine`` reference is resolved at call time, so the
    TDD suite can monkey-patch ``taskq_api.app.create_engine`` to
    simulate a DB outage.
    """
    db_url = os.environ.get("TASKQ_DB_URL", "")
    if not db_url:
        # Nothing to probe — state is undetermined, not known-behind.
        return False, _readyz_detail(_UNKNOWN_PREFIX, "no TASKQ_DB_URL configured")

    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            row = conn.execute(
                sql_text("SELECT version_num FROM alembic_version")
            ).first()
    except Exception as exc:
        # SPEC §8 #10 — fail closed when the probe cannot reach the DB
        # or the alembic metadata table is absent. Only the exception
        # CLASS is surfaced; the message can embed the DSN and would
        # leak the password past the NFR-04 redaction boundary.
        return False, _readyz_detail(
            _UNKNOWN_PREFIX,
            f"db probe failed: {type(exc).__name__}",
        )

    if row is None:
        # Table exists but was never stamped — a real, confirmed
        # migration gap (SPEC §8 #11), not a DB fault.
        return False, _readyz_detail(
            _BEHIND_HEAD_PREFIX,
            f"alembic_version has no row; expected head={_MIGRATION_HEAD}",
        )

    current = row[0]
    if current != _MIGRATION_HEAD:
        return (
            False,
            _readyz_detail(
                _BEHIND_HEAD_PREFIX,
                f"current={current}, head={_MIGRATION_HEAD}",
            ),
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


def _build_lifespan() -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """[FR-08] Lifespan that bound-runs the TaskRunner graceful drain.

    Citations:
    - SPEC.md §3 FR-08 — on shutdown, await the runner's
      ``shutdown(drain_timeout_seconds)`` so in-flight tasks are
      drained (or marked ``status='interrupted'``) and no orphan
      pids survive the process exit.
    - SPEC.md §3 FR-08 — ``TASKQ_DRAIN_TIMEOUT`` is the bounded
      window the composition root enforces on the runner.
    """
    drain_timeout_seconds = float(os.environ.get("TASKQ_DRAIN_TIMEOUT", "5"))

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        runner = TaskRunner()
        try:
            yield
        finally:
            # FR-08 — graceful drain; stragglers past the bounded
            # window get marked 'interrupted' (SPEC §3 FR-08). The
            # runner's ``shutdown`` is sync (per FR-02 contract) but
            # tests may install an async mock; handle both shapes.
            result = runner.shutdown(drain_timeout_seconds=drain_timeout_seconds)
            if asyncio.iscoroutine(result):
                await result

    # ``@asynccontextmanager`` wraps the async generator so the
    # returned callable takes ``FastAPI`` and yields an
    # ``AbstractAsyncContextManager[None]``; pyright sees the
    # decorator's return type (``_AsyncGeneratorContextManager``)
    # rather than ``Callable[[FastAPI], …]``, so we cast through the
    # protocol FastAPI's ``lifespan=`` parameter expects.
    _f = cast(Callable[[FastAPI], AbstractAsyncContextManager[None]], _lifespan)
    return _f


def create_app() -> FastAPI:
    """Construct the FastAPI application for FR-01 / FR-03 / FR-04 / FR-05 / FR-07 / FR-08 / FR-09."""
    app = FastAPI(
        title="taskq-api",
        version="0.1.0",
        description=(
            "HTTP task-queue service (FR-01 / FR-03 / FR-04 / FR-05 / FR-07 / FR-08 / FR-09 GREEN step)."
        ),
        lifespan=_build_lifespan(),
    )
    register_error_handlers(app)
    _flat_include_router(app, create_tasks_router())

    # ------------------------------------------------------------------
    # /healthz, /readyz — FR-05 + FR-07 + FR-09.
    #
    # The health router is mounted DIRECTLY on `app` (via the flat
    # include helper) so the per-route `require_scope`/rate-limit
    # dependency chain never fires for these probes. SPEC §3 FR-05
    # explicitly states "/healthz, /readyz 不受限" (not subject to
    # the per-token bucket); mounting at the app level is the
    # simplest way to honour that — every /v1/* route goes through
    # `deps.get_current_key` (which consults the bucket), while
    # /healthz and /readyz do not.
    #
    # The readiness probe (``/readyz``) delegates to
    # ``_check_migration_state`` — the SQLAlchemy-using helper
    # defined above. The helper resolves ``create_engine`` from this
    # module's globals at call time, so the TDD suite's monkey-patch
    # of ``taskq_api.app.create_engine`` takes effect (SPEC §8 #10).
    # ------------------------------------------------------------------
    _flat_include_router(app, create_health_router(probe=_check_migration_state))

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


__all__ = ["create_app", "app", "_build_metrics_body", "_check_migration_state"]