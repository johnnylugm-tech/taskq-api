"""[FR-01] Composition root — FastAPI app factory.

Citations:
- SPEC.md §3 FR-01 — `POST /v1/tasks` mounted under `/v1`.
- SAD.md §2.8 — `app.py` lives next to `api/health.py` (the hub) and
  includes every router.
- SAD.md §3.1 — middleware/error handlers registered here.
"""
from __future__ import annotations

from fastapi import FastAPI

from taskq_api.api.tasks import create_tasks_router
from taskq_api.errors import register_error_handlers


def create_app() -> FastAPI:
    """Construct the FastAPI application for FR-01."""
    app = FastAPI(
        title="taskq-api",
        version="0.1.0",
        description="HTTP task-queue service (FR-01 GREEN step).",
    )
    register_error_handlers(app)
    app.include_router(create_tasks_router())
    return app


# Module-level binding for `uvicorn taskq_api.app:app` — accepted by
# Phase-3 conformance scripts that probe for `app: FastAPI`.
app = create_app()


__all__ = ["create_app", "app"]
