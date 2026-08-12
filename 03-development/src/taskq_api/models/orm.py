"""[FR-02, FR-03, FR-07] ORM models for the task execution subsystem.

Citations:
- SPEC.md §3 FR-02 — ``task_results`` table persists one row per
  task run; columns: ``exit_code`` / ``stdout_tail`` / ``stderr_tail``
  / ``duration_ms`` / ``finished_at`` (FR-07 v3 schema).
- SPEC.md §3 FR-03 — ``api_keys`` table stores SHA-256 hashes only
  (64 lowercase hex chars); no plaintext column is ever exposed.
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


# API-key row fields — FR-03 mandates the hash only (64 lowercase hex
# chars); the plaintext is NEVER persisted and therefore cannot be a
# column on this ORM row.
_KEY_ROW_FIELDS: tuple[str, ...] = (
    "id",
    "scope",
    "key_hash",
    "revoked_at",
)


_ROW_FIELDS: tuple[str, ...] = (
    "id",
    "task_id",
    "run_id",
    "exit_code",
    "stdout_tail",
    "stderr_tail",
    "duration_ms",
    "finished_at",
    "status",
)


class ApiKey:
    """[FR-03] ORM row for the ``api_keys`` table.

    Citations:
    - SPEC.md §3 FR-03 — stores only the SHA-256 hash of the
      plaintext key; the plaintext is never persisted and never
      exposed as an attribute on this row.
    - SAD.md §2.4 — `api_keys` aggregate per-table ORM module.
    - SPEC.md §3 FR-03 — `revoked_at` non-null means the key is
      rejected (AC6-revoked-status).
    """

    def __init__(
        self,
        *,
        id: Optional[str] = None,
        scope: str,
        key_hash: str,
        revoked_at: Optional[str] = None,
    ) -> None:
        self.id = id or str(uuid.uuid4())
        self.scope = scope
        self.key_hash = key_hash
        self.revoked_at = revoked_at

    def __repr__(self) -> str:
        return (
            f"ApiKey(id={self.id!r}, scope={self.scope!r}, "
            f"key_hash={self.key_hash!r}, revoked_at={self.revoked_at!r})"
        )


__all__ = ["ApiKey", "TaskResult"]


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
    def _from_dict(cls, row: dict[str, Any]) -> "TaskResult":
        """Rehydrate a row from the in-process registry."""
        return cls(**{field: row[field] for field in _ROW_FIELDS})

    @classmethod
    def add(cls, row: "TaskResult") -> None:
        """Persist a result row in the in-process registry."""
        cls._registry.append({field: getattr(row, field) for field in _ROW_FIELDS})

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
        return [cls._from_dict(r) for r in rows]
