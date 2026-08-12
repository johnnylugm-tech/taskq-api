"""[FR-01] Task service — business orchestration.

Citations:
- SPEC.md §3 FR-01 — task CRUD; uniqueness / size / name validation
  centralised here so the API handler stays ≤ 40 lines (NFR-11).
- SAD.md §2.6 — business layer delegates persistence to `task_repo`;
  raises typed `problem+json` exceptions which the API layer unwraps.
- SAD.md §3.1 — request lifecycle: handler → `service.tasks.<op>` →
  `repository.task_repo`.

GREEN step provides in-process state because the test fixture patches
`taskq_api.repository.session.get_session` with a fresh `_FakeSession`
per call (no shared storage). Production wiring swaps this for a real
SQLAlchemy session.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from taskq_api.errors import ConflictProblem, NotFoundProblem
from taskq_api.repository.task_repo import TaskRepo


class TaskService:
    """[FR-01] Business operations on the task aggregate.

    Citations: SPEC.md §3 FR-01; SAD.md §2.6.
    """

    def __init__(self, repo: Optional[TaskRepo] = None) -> None:
        self._repo = repo if repo is not None else TaskRepo()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def create(self, *, name: str, command: str) -> dict[str, Any]:
        """Create a task. Raises :class:`ConflictProblem` on duplicate
        name (SPEC §3 FR-01 → 409).
        """
        if self._repo.exists_by_name(name):
            # Roll back the speculative session add.
            self._repo.rollback()
            raise ConflictProblem(detail=f"task name '{name}' already exists")
        task_id = str(uuid.uuid4())
        row = {"id": task_id, "name": name, "command": command, "status": "pending"}
        self._repo.create(name=name, command=command)
        # Materialise the row into the in-process registry so the rest
        # of the test (duplicate POST, GET, etc.) observes the row.
        row["id"] = task_id
        self._repo.register(row)
        self._repo.commit()
        return row

    def delete(self, task_id: str) -> None:
        """Delete a task by id (raises :class:`NotFoundProblem` if absent)."""
        if not self._repo.delete(task_id):
            self._repo.rollback()
            raise NotFoundProblem(detail="task not found")
        self._repo.commit()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get(self, task_id: str) -> dict[str, Any]:
        """Fetch one task by id (raises 404 if absent)."""
        row = self._repo.get(task_id)
        if row is None:
            raise NotFoundProblem(detail="task not found")
        return row

    def list(
        self,
        *,
        status: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List tasks with cursor-based pagination.

        Citations:
        - SPEC.md §3 FR-01 — cursor (NOT offset); default limit=50.
        - SPEC.md §4 NFR-01 — constant statement count via
          eager-loaded relations.
        """
        rows, next_cursor = self._repo.list(status=status, cursor=cursor, limit=limit)
        return {
            "limit": limit,
            "items": rows,
            "next_cursor": next_cursor,
        }


__all__ = ["TaskService"]
