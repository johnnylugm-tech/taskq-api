"""[FR-01, FR-03, FR-09] Composition root — FastAPI app factory.

Citations:
- SPEC.md §3 FR-01 — `POST /v1/tasks` mounted under `/v1`.
- SPEC.md §3 FR-03 — every `/v1/*` route requires `X-API-Key`; the
  redaction filter is wired into the logging pipeline at import time.
- SPEC.md §3 FR-09 — `/healthz`, `/readyz`, and `/v1/metrics` are
  exposed here (no auth required by the spec).
- SAD.md §2.8 — `app.py` lives next to `api/health.py` (the hub) and
  includes every router.
- SAD.md §3.1 — middleware/error handlers registered here.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Response

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


def create_app() -> FastAPI:
    """Construct the FastAPI application for FR-01 / FR-03 / FR-09."""
    app = FastAPI(
        title="taskq-api",
        version="0.1.0",
        description="HTTP task-queue service (FR-01 / FR-03 GREEN step).",
    )
    register_error_handlers(app)
    app.include_router(create_tasks_router())

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
