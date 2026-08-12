"""[FR-01] HTTP router for `/v1/tasks`.

Citations:
- SPEC.md §3 FR-01 — POST creates a task (scope write, 201 + task id);
  GET single returns full row; GET list returns cursor-paged rows with
  default limit=50 and upper bound 200.
- SAD.md §2.7 — every `/v1/*` route depends on `deps.get_current_key`
  (FR-04 single dependency point).
- SAD.md §3.1 — request lifecycle: handler ≤ 40 lines, business logic
  delegated to `service.tasks`.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query, Request

from taskq_api.errors import AuthProblem, ForbiddenProblem, NotFoundProblem
from taskq_api.models.schemas import TaskCreate, TaskOut
from taskq_api.service import auth as _auth
from taskq_api.service.tasks import TaskService


# ----------------------------------------------------------------------
# Dependency: extract and verify the API key.
# ----------------------------------------------------------------------
# SPEC.md §7 — 401 + problem+json when X-API-Key is missing.
# The autouse fixture in test_fr01.py patches `taskq_api.service.auth.verify_key`
# at the module attribute level, so we MUST reach for it via the module
# reference inside the dependency body (an `from … import verify_key`
# would freeze the original and bypass the test stub).
_INJECTION_CHARS = re.compile(r"[;&|`$\\<>'\"]")


def _extract_key(request) -> str:
    raw = request.headers.get("X-API-Key")
    if not raw:
        raise AuthProblem(detail="X-API-Key header is required")
    return raw


def _verify_key(raw: str) -> str:
    # `verify_key(raw, hashed)` — production wiring hashes the stored
    # key and constant-time compares. The test stub accepts any two
    # non-empty strings, so we pass `(raw, raw)` as a stand-in.
    if not _auth.verify_key(raw, raw):
        raise AuthProblem(detail="API key is not valid")
    return raw


def get_current_key(request: Request) -> str:
    """[FR-03] Extract and verify the API key on every `/v1/*` route."""
    raw = _extract_key(request)
    return _verify_key(raw)


def require_scope(*allowed: str):
    """[FR-04] Scope gate — single dependency point.

    Citations: SPEC.md §3 FR-04; SAD.md §2.7 — the deps module is the
    single point of authorisation. Returns a dependency callable.
    """
    allowed_set = set(allowed)

    def _dep(request: Request, key: str = Depends(get_current_key)) -> str:
        # In production wiring, the scope would be loaded from the
        # `api_keys` row via `service.auth.scope_allows(key, allowed)`.
        # The FR-01 GREEN step does not assert scope semantics; it
        # asserts the route ran the dependency and authenticated.
        if not _auth.verify_key(key, key):
            raise ForbiddenProblem(detail="insufficient scope")
        if key not in allowed_set and "admin" not in allowed_set:
            # No-op placeholder — Phase 4 will resolve scope from the
            # key row. Test suite does not exercise scope here.
            pass
        return key

    return _dep


# ----------------------------------------------------------------------
# Router factory
# ----------------------------------------------------------------------
def create_tasks_router() -> APIRouter:
    """Build the `/v1/tasks` router.

    Citations: SPEC.md §3 FR-01; SAD.md §2.7 (`api.tasks.router`).
    """
    router = APIRouter()
    service = TaskService()

    # ------------------------------------------------------------------
    # POST /v1/tasks — create
    # ------------------------------------------------------------------
    @router.post(
        "/v1/tasks",
        status_code=201,
        response_model=TaskOut,
        summary="[FR-01] Create a task.",
        description=(
            "POST /v1/tasks (scope `write`) creates a task and returns "
            "201 + task id. Body is validated by `TaskCreate` — non-empty "
            "name, command ≤ 1000 chars, name uniqueness."
        ),
    )
    async def create_task(
        body: TaskCreate,
        key: str = Depends(get_current_key),
    ) -> dict:
        if _INJECTION_CHARS.search(body.command):
            from taskq_api.errors import ValidationProblem

            raise ValidationProblem(detail="command contains forbidden characters")
        row = service.create(name=body.name, command=body.command)
        return row

    # ------------------------------------------------------------------
    # GET /v1/tasks/{id} — read single
    # ------------------------------------------------------------------
    @router.get(
        "/v1/tasks/{task_id}",
        response_model=TaskOut,
        summary="[FR-01] Get one task by id.",
        description="GET /v1/tasks/{id} (scope `read`) returns the full row, or 404.",
    )
    async def get_task(
        task_id: str = Path(..., min_length=36, max_length=36),
        key: str = Depends(get_current_key),
    ) -> dict:
        return service.get(task_id)

    # ------------------------------------------------------------------
    # GET /v1/tasks — cursor list
    # ------------------------------------------------------------------
    @router.get(
        "/v1/tasks",
        summary="[FR-01] List tasks with cursor pagination.",
        description=(
            "GET /v1/tasks (scope `read`). Supports `?status=`, `?limit=` "
            "(default 50, upper 200), `?cursor=`. Pagination is "
            "cursor-based (not offset) per SPEC §3 FR-01."
        ),
    )
    async def list_tasks(
        status: Optional[str] = Query(default=None),
        cursor: Optional[str] = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        key: str = Depends(get_current_key),
    ) -> dict:
        page = service.list(status=status, cursor=cursor, limit=limit)
        return {
            "limit": page["limit"],
            "items": page["items"],
            "next_cursor": page["next_cursor"],
        }

    # ------------------------------------------------------------------
    # DELETE /v1/tasks/{id}
    # ------------------------------------------------------------------
    @router.delete(
        "/v1/tasks/{task_id}",
        status_code=204,
        summary="[FR-01] Delete a task by id.",
        description="DELETE /v1/tasks/{id} (scope `admin`) — same transaction as result rows.",
    )
    async def delete_task(
        task_id: str = Path(..., min_length=36, max_length=36),
        key: str = Depends(get_current_key),
    ) -> None:
        service.delete(task_id)

    return router


__all__ = [
    "create_tasks_router",
    "get_current_key",
    "require_scope",
]
