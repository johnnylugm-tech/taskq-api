"""Task resource business rules.

[FR-01] [FR-02]
Citations: SPEC.md lines 79-91, 93-100; SRS.md lines 78-86, 88-105.
"""

from __future__ import annotations

from typing import Any

from taskq_api.errors import ConflictProblem, NotFoundProblem
from taskq_api.models.schemas import TaskCreate
from taskq_api.repository.task_repo import TaskRepository
from taskq_api.service.runner import execute_task, list_runs

_TASK_NOT_FOUND_DETAIL = "Task not found"


class TaskService:
    """Coordinate task CRUD and uniqueness rules. [FR-01]

    Citations: SPEC.md lines 79-91.
    """

    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository

    def create(self, payload: TaskCreate) -> dict[str, str]:
        """Create a task after enforcing name uniqueness. [FR-01]

        Citations: SPEC.md lines 81, 88.
        """
        if self._repository.has_name(payload.name):
            raise ConflictProblem("A task with that name already exists")
        return self._repository.create(payload.command, payload.name)

    def get(self, task_id: str) -> dict[str, str]:
        """Get an existing task. [FR-01]

        Citations: SPEC.md lines 82, 89.
        """
        task = self._repository.get(task_id)
        if task is None:
            raise NotFoundProblem(_TASK_NOT_FOUND_DETAIL)
        return task

    def list_by_cursor(
        self, status: str | None, limit: int, cursor: str | None
    ) -> dict[str, object]:
        """Return a bounded cursor page. [FR-01]

        Citations: SPEC.md lines 83, 90-91.
        """
        items, next_cursor = self._repository.list_by_cursor(status, limit, cursor)
        return {"items": items, "next_cursor": next_cursor}

    def delete(self, task_id: str) -> None:
        """Delete a task or report that it is absent. [FR-01]

        Citations: SPEC.md lines 84, 89.
        """
        if not self._repository.delete(task_id):
            raise NotFoundProblem(_TASK_NOT_FOUND_DETAIL)

    async def run(self, task_id: str) -> str:
        """Execute a task and return the identifier of its run. [FR-02]

        Citations: SPEC.md lines 95-98; SRS.md lines 93-103.
        """
        task = self.get(task_id)
        result = await execute_task(task, self._repository)
        return str(result["run_id"])

    def runs(self, task_id: str) -> dict[str, Any]:
        """Return a task's execution history, newest first. [FR-02]

        Citations: SPEC.md line 99; SRS.md lines 104-105.
        """
        self.get(task_id)
        return {"items": list_runs(task_id)}
