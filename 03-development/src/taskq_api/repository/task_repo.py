"""In-memory task persistence boundary.

[FR-01]
Citations: SPEC.md lines 79-91; SAD.md line 107.
"""

from __future__ import annotations

import base64
import binascii
import uuid


class TaskRepository:
    """Persist tasks and perform keyset pagination. [FR-01]

    Citations: SPEC.md lines 79-91.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, str]] = {}
        self._ordered_ids: list[str] = []
        self._names: set[str] = set()

    def create(self, command: str, name: str) -> dict[str, str]:
        """Insert and return a task record. [FR-01]

        Citations: SPEC.md lines 81, 88.
        """
        task_id = str(uuid.uuid4())
        task = {"id": task_id, "command": command, "name": name, "status": "pending"}
        self._tasks[task_id] = task
        self._ordered_ids.append(task_id)
        self._names.add(name)
        return task.copy()

    def has_name(self, name: str) -> bool:
        """Check name uniqueness without exposing tenant data. [FR-01]

        Citations: SPEC.md line 88.
        """
        return name in self._names

    def get(self, task_id: str) -> dict[str, str] | None:
        """Read by validated UUID key in constant time. [FR-01]

        Citations: SPEC.md lines 82, 89.
        """
        try:
            normalized_id = str(uuid.UUID(task_id))
        except (ValueError, AttributeError):
            return None
        task = self._tasks.get(normalized_id)
        return task.copy() if task is not None else None

    def list_by_cursor(
        self, status: str | None, limit: int, cursor: str | None
    ) -> tuple[list[dict[str, str]], str | None]:
        """Read the next page after an opaque keyset cursor. [FR-01]

        Citations: SPEC.md lines 83, 90-91.
        """
        eligible_ids = [
            task_id
            for task_id in self._ordered_ids
            if status is None or self._tasks[task_id]["status"] == status
        ]
        start = self._cursor_start(eligible_ids, cursor)
        page_ids = eligible_ids[start : start + limit]
        items = [self._tasks[task_id].copy() for task_id in page_ids]
        has_more = start + len(page_ids) < len(eligible_ids)
        next_cursor = self._encode_cursor(page_ids[-1]) if has_more and page_ids else None
        return items, next_cursor

    def delete(self, task_id: str) -> bool:
        """Delete a task and its keyset index entry. [FR-01]

        Citations: SPEC.md lines 84, 89.
        """
        task = self.get(task_id)
        if task is None:
            return False
        normalized_id = task["id"]
        del self._tasks[normalized_id]
        self._ordered_ids.remove(normalized_id)
        self._names.remove(task["name"])
        return True

    @staticmethod
    def _encode_cursor(task_id: str) -> str:
        return base64.urlsafe_b64encode(task_id.encode()).decode().rstrip("=")

    @classmethod
    def _cursor_start(cls, eligible_ids: list[str], cursor: str | None) -> int:
        if cursor is None:
            return 0
        try:
            padding = "=" * (-len(cursor) % 4)
            task_id = base64.urlsafe_b64decode(cursor + padding).decode()
            return eligible_ids.index(task_id) + 1
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return len(eligible_ids)
