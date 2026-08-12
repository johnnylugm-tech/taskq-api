"""[FR-01, FR-02] HTTP router for `/v1/tasks`.

Citations:
- SPEC.md §3 FR-01 — POST creates a task (scope write, 201 + task id);
  GET single returns full row; GET list returns cursor-paged rows with
  default limit=50 and upper bound 200.
- SPEC.md §3 FR-02 — POST /v1/tasks/{id}/run (scope write) returns
  202 + run_id; the runner executes the command via
  ``shlex.split`` + ``asyncio.create_subprocess_exec`` with
  ``shell=False``.
- SAD.md §2.7 — every `/v1/*` route depends on `deps.get_current_key`
  (FR-04 single dependency point).
- SAD.md §3.1 — request lifecycle: handler ≤ 40 lines, business logic
  delegated to `service.tasks` / `service.runner`.
"""
from __future__ import annotations

import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query

from taskq_api.api.deps import get_current_key, require_scope
from taskq_api.errors import ValidationProblem
from taskq_api.models.orm import TaskResult
from taskq_api.models.schemas import TaskCreate, TaskOut
from taskq_api.service.runner import TaskRunner
from taskq_api.service.tasks import TaskService


# Auth/scope dependencies live in `api.deps` (SAD.md §2.7 — the single
# dependency point). They are imported here so the routes below can
# declare them, and re-exported via `__all__` for callers that still
# reach for `api.tasks.get_current_key` / `api.tasks.require_scope`.

# SPEC.md §8 #16 — reject shell metacharacters in the submitted command
# before it ever reaches the runner.
_INJECTION_CHARS = re.compile(r"[;&|`$\\<>'\"]")


def _result_from_runner_record(
    *,
    task_id: str,
    run_id: str,
    record: dict,
) -> TaskResult:
    """[FR-02] Build a TaskResult ORM row from a runner record dict.

    The runner returns a dict of execution fields; this helper fills in
    the row's identity (task_id / run_id) and applies defaults for any
    optional fields the runner may omit (FR-08 `status='interrupted'`,
    FR-08 `status='timeout'`).
    """
    return TaskResult(
        task_id=task_id,
        run_id=run_id,
        exit_code=record.get("exit_code"),
        stdout_tail=record.get("stdout_tail", ""),
        stderr_tail=record.get("stderr_tail", ""),
        duration_ms=record.get("duration_ms", 0),
        finished_at=record.get("finished_at", ""),
        status=record.get("status", "done"),
    )


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
            raise ValidationProblem(detail="command contains forbidden characters")
        return service.create(name=body.name, command=body.command)

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

    # ------------------------------------------------------------------
    # POST /v1/tasks/{id}/run — execute a task (FR-02)
    # ------------------------------------------------------------------
    @router.post(
        "/v1/tasks/{task_id}/run",
        status_code=202,
        summary="[FR-02] Run a task asynchronously.",
        description=(
            "POST /v1/tasks/{id}/run (scope `write`) returns 202 + "
            "`run_id`. The runner executes the task command via "
            "`shlex.split` + `asyncio.create_subprocess_exec` with "
            "`shell=False` (SPEC §3 FR-02 / §8 #16)."
        ),
    )
    async def run_task(
        task_id: str = Path(..., min_length=36, max_length=36),
        key: str = Depends(get_current_key),
    ) -> dict:
        # 404 if the target task does not exist (AC7-unknown-run).
        task = service.get(task_id)
        run_id = str(uuid.uuid4())
        record = await TaskRunner().run(task["command"])
        result = _result_from_runner_record(task_id=task_id, run_id=run_id, record=record)
        TaskResult.add(result)
        return {"run_id": run_id, "status": result.status}

    # ------------------------------------------------------------------
    # GET /v1/tasks/{id}/runs — run history (FR-02)
    # ------------------------------------------------------------------
    @router.get(
        "/v1/tasks/{task_id}/runs",
        summary="[FR-02] List a task's run history.",
        description=(
            "GET /v1/tasks/{id}/runs (scope `read`) returns the "
            "task's execution records sorted newest-first."
        ),
    )
    async def list_runs(
        task_id: str = Path(..., min_length=36, max_length=36),
        key: str = Depends(get_current_key),
    ) -> dict:
        # 404 if the target task does not exist.
        service.get(task_id)
        rows = TaskResult.list_for_task(task_id)
        return {
            "items": [
                {
                    "run_id": r.run_id,
                    "status": r.status,
                    "exit_code": r.exit_code,
                    "stdout_tail": r.stdout_tail,
                    "stderr_tail": r.stderr_tail,
                    "duration_ms": r.duration_ms,
                    "finished_at": r.finished_at,
                }
                for r in rows
            ],
        }

    return router


__all__ = [
    "create_tasks_router",
    "get_current_key",
    "require_scope",
]
