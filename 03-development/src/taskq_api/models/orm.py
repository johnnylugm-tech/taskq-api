"""[FR-02, FR-07] ORM models for the task execution subsystem.

Citations:
- SPEC.md §3 FR-02 — ``task_results`` table persists one row per
  task run; columns: ``exit_code`` / ``stdout_tail`` / ``stderr_tail``
  / ``duration_ms`` / ``finished_at`` (FR-07 v3 schema).
- SAD.md §2.4 — ``models/orm.py`` is the per-table ORM module
  consumed by both the repository layer (real SQLAlchemy session)
  and the test suite (in-process registry).
- SPEC.md §3 FR-08 — ``status`` column records the lifecycle state
  (``done`` / ``failed`` / ``timeout`` / ``interrupted``).

GREEN step keeps rows in an in-process registry so the failing
test suite (``test_fr02.py``) can observe persistence without a
live database. The autouse fixture in ``conftest.py`` clears
``TaskRepo._registry``; ``TaskResult`` rows are filtered by
``task_id`` so per-test isolation is preserved even when rows
accumulate across tests.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional


class TaskResult:
    """[FR-02] ORM row for the ``task_results`` table (FR-07 v3 schema).

    Citations: SPEC.md §3 FR-02 (one row per run); SPEC.md §3 FR-07
    v3 schema columns.
    """

    # Module-level registry — backs the failing test suite which
    # provides a fresh ``_FakeSession`` per call (no shared state
    # across calls). Production wiring replaces this with a real
    # SQLAlchemy model bound to the configured engine.
    _registry: list[dict[str, Any]] = []

    def __init__(
        self,
        *,
        id: Optional[str] = None,
        task_id: str,
        run_id: str,
        exit_code: Optional[int] = None,
        stdout_tail: str = "",
        stderr_tail: str = "",
        duration_ms: int = 0,
        finished_at: str = "",
        status: str = "done",
    ) -> None:
        self.id = id or str(uuid.uuid4())
        self.task_id = task_id
        self.run_id = run_id
        self.exit_code = exit_code
        self.stdout_tail = stdout_tail
        self.stderr_tail = stderr_tail
        self.duration_ms = duration_ms
        self.finished_at = finished_at
        self.status = status

    # ------------------------------------------------------------------
    # Persistence (in-process registry)
    # ------------------------------------------------------------------
    @classmethod
    def add(cls, row: "TaskResult") -> None:
        """Persist a result row in the in-process registry."""
        cls._registry.append(
            {
                "id": row.id,
                "task_id": row.task_id,
                "run_id": row.run_id,
                "exit_code": row.exit_code,
                "stdout_tail": row.stdout_tail,
                "stderr_tail": row.stderr_tail,
                "duration_ms": row.duration_ms,
                "finished_at": row.finished_at,
                "status": row.status,
            }
        )

    @classmethod
    def list_for_task(cls, task_id: str) -> list["TaskResult"]:
        """[FR-02] Return all results for ``task_id``, newest-first.

        Citations: SPEC.md §3 FR-02 — ``GET /v1/tasks/{id}/runs``
        returns history sorted new-to-old.
        """
        rows = [r for r in cls._registry if r.get("task_id") == task_id]
        # Newest-first by insertion order (Python 3.7+ preserves dict
        # insertion order; the runner appends in run-completion order).
        rows.reverse()
        return [
            cls(
                id=r["id"],
                task_id=r["task_id"],
                run_id=r["run_id"],
                exit_code=r["exit_code"],
                stdout_tail=r["stdout_tail"],
                stderr_tail=r["stderr_tail"],
                duration_ms=r["duration_ms"],
                finished_at=r["finished_at"],
                status=r["status"],
            )
            for r in rows
        ]


__all__ = ["TaskResult"]
