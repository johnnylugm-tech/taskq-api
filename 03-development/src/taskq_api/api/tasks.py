"""Task API transport handlers.

[FR-01] [FR-02]
Citations: SPEC.md lines 79-91, 93-100; SRS.md lines 78-86, 88-105.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response, status

from taskq_api.models.schemas import TaskCreate, TaskListQuery
from taskq_api.service.tasks import TaskService

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


def _get_task_service(request: Request) -> TaskService:
    return request.app.state.task_service


@router.post("", status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, request: Request) -> dict[str, str]:
    """Create a validated task. [FR-01]

    Citations: SPEC.md lines 81, 88.
    """
    return _get_task_service(request).create(payload)


@router.get("")
def list_tasks(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(
        default=TaskListQuery.DEFAULT_LIMIT,
        ge=1,
        le=TaskListQuery.MAX_LIMIT,
    ),
    cursor: str | None = Query(default=None),
) -> dict[str, object]:
    """List tasks using an opaque keyset cursor. [FR-01]

    Citations: SPEC.md lines 83, 90-91.
    """
    return _get_task_service(request).list_by_cursor(status_filter, limit, cursor)


@router.get("/{task_id}")
def get_task(task_id: str, request: Request) -> dict[str, str]:
    """Return one task or a not-found problem. [FR-01]

    Citations: SPEC.md lines 82, 89.
    """
    return _get_task_service(request).get(task_id)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: str, request: Request) -> Response:
    """Delete one task and its associated state. [FR-01]

    Citations: SPEC.md lines 84, 89.
    """
    _get_task_service(request).delete(task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{task_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_task(task_id: str, request: Request) -> dict[str, str]:
    """Launch a task run and acknowledge it with its run id. [FR-02]

    Citations: SPEC.md line 95; SRS.md lines 93-95.
    """
    return {"run_id": await _get_task_service(request).run(task_id)}


@router.get("/{task_id}/runs")
def list_task_runs(task_id: str, request: Request) -> dict[str, object]:
    """Return a task's execution history, newest first. [FR-02]

    Citations: SPEC.md line 99; SRS.md lines 104-105.
    """
    return _get_task_service(request).runs(task_id)
