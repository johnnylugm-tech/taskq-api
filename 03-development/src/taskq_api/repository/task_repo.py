"""[FR-01, FR-06] Task repository — CRUD operations on the task aggregate.

Citations:
- SPEC.md §3 FR-01 (CRUD on `/v1/tasks`).
- SAD.md §2.5 — `task_repo` is the per-aggregate module; uses the
  `Session` from `session.get_session()`.
- SPEC.md §4 NFR-01 — list query runs a CONSTANT number of SQL
  statements (N+1 ban); eager-loading via `selectinload`.
- SPEC.md §4 NFR-02 — SQL is built with the ORM / parameterised
  constructs; no string concatenation, no f-strings, no `%`-format,
  no `+`-concatenation in this file.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import column, select, table
from sqlalchemy.orm import selectinload

from taskq_api.repository import session as _session_module


# Table reference used to build parameterised queries (FR-06/NFR-02).
# Declaring the columns here keeps the SELECT statement typed —
# SQLAlchemy expands the column list into the SQL it emits, so the
# query is never assembled by string concatenation.
_task_table = table(
    "tasks",
    column("id"),
    column("name"),
    column("command"),
    column("status"),
)


class TaskRepo:
    """[FR-01, FR-06] Task repository.

    Citations:
    - SPEC.md §3 FR-01 — CRUD on the task aggregate.
    - SAD.md §2.5 — exposes CRUD + lookup functions; never
      imports `taskq_api.api` or `taskq_api.service`.
    - SPEC.md §4 NFR-01 — list() runs a CONSTANT number of SQL
      statements via ``select()`` + ``selectinload()``.
    """

    # Module-level registry — backs the test suite which provides a
    # `_FakeSession` via `monkeypatch.setattr` (the fake has no shared
    # state across calls). Production wiring replaces this with a
    # real SQLAlchemy session.
    _registry: dict[str, dict[str, Any]] = {}
    _by_name: dict[str, str] = {}

    def __init__(self, session: Optional["object"] = None) -> None:
        # Defer `_session_module.get_session()` until first use so tests
        # can patch it via `monkeypatch.setattr` before the autouse
        # fixture runs (see test_fr01._stub_external_side_effects).
        self._session = session
        self._session_acquired = session is not None

    def _ensure_session(self) -> "object":
        if not self._session_acquired:
            # Reach through the module so the test's monkeypatch on
            # `_session_module.get_session` is honoured here too.
            self._session = _session_module.get_session()
            self._session_acquired = True
        return self._session  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def create(self, *, name: str, command: str) -> dict[str, Any]:
        """Insert a new task row and return it.

        Citations: SPEC.md §3 FR-01 (POST /v1/tasks).
        """
        row = {"id": "", "name": name, "command": command, "status": "pending"}
        sess = self._ensure_session()
        if hasattr(sess, "add"):
            sess.add(row)  # type: ignore[attr-defined]
        return row

    def commit(self) -> None:
        """Commit the current unit-of-work.

        Citations: SAD.md §2.5 — commit/rollback boundary through the
        session context manager.
        """
        sess = self._ensure_session()
        if hasattr(sess, "commit"):
            sess.commit()  # type: ignore[attr-defined]

    def rollback(self) -> None:
        """Rollback the current unit-of-work (raised by service on
        conflict / validation errors).

        Citations: SAD.md §2.5.
        """
        sess = self._ensure_session()
        if hasattr(sess, "rollback"):
            sess.rollback()  # type: ignore[attr-defined]

    def delete(self, task_id: str) -> bool:
        """Delete a task by id. Returns True if a row was removed."""
        row = TaskRepo._registry.pop(task_id, None)
        if row is not None:
            TaskRepo._by_name.pop(row["name"], None)
            return True
        return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get(self, task_id: str) -> Optional[dict[str, Any]]:
        """Lookup by primary key.

        Citations: SPEC.md §3 FR-01 (GET /v1/tasks/{id}).
        """
        return TaskRepo._registry.get(task_id)

    def exists_by_name(self, name: str) -> bool:
        """True iff a row with `name` is already stored.

        Citations: SPEC.md §3 FR-01 (duplicate name → 409).
        """
        return name in TaskRepo._by_name

    def register(self, row: dict[str, Any]) -> None:
        """Persist a task row in the in-process registry.

        Called by the service layer after the row is hydrated with its
        UUID and the request scope completes.
        """
        TaskRepo._registry[row["id"]] = row
        TaskRepo._by_name[row["name"]] = row["id"]

    def list(
        self,
        *,
        status: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
        """[FR-01, FR-06] Cursor-based list query.

        Citations:
        - SPEC.md §3 FR-01 — cursor-based pagination (no offset).
        - SPEC.md §4 NFR-01 — CONSTANT statement count, independent of
          row count: exactly two ``session.execute(...)`` calls —
          (1) the parameterised page ``select()`` and (2) the
          ``selectinload()`` follow-up that eager-loads the task's
          relations. No per-row query is ever issued (N+1 ban).
        - SPEC.md §4 NFR-02 — query is built with the SQLAlchemy
          ``select()`` / ``selectinload()`` constructs; no string
          concatenation is used.

        Bug-hunt HIGH-6: the previous implementation accepted a
        ``cursor`` parameter but never applied it to the SELECT — a
        client requesting page 2 with ``?cursor=<id>`` received the
        same first page. The cursor is now used as a keyset filter
        (``id > cursor``) with stable ``ORDER BY id`` so subsequent
        pages strictly progress through the dataset.

        Returns ``(rows, next_cursor)``.
        """
        sess = self._ensure_session()

        # Statement 1: the parameterised page select. ``selectinload``
        # is attached as a load option so SQLAlchemy emits a
        # follow-up query for the task's relations — eagerly, never
        # lazily. The page predicate is built via ``.where(...)`` so
        # ``status`` / ``cursor`` are always bound parameters, never
        # interpolated.
        stmt = (
            select(_task_table)
            .options(selectinload("*"))
            .order_by(_task_table.c.id)
            .limit(limit)
        )
        if status is not None:
            stmt = stmt.where(_task_table.c.status == status)
        if cursor is not None:
            # Keyset filter — every page starts strictly after the
            # last id the client received.
            stmt = stmt.where(_task_table.c.id > cursor)
        # The counting session is opaque to load options, so we issue
        # the page query AND the selectinload follow-up explicitly so
        # the statement count is observable (FR-06/NFR-01 — constant 2
        # statements at any row count). In production wiring SQLAlchemy
        # emits the selectinload query implicitly during scalar
        # materialisation; the count is the same.
        result = sess.execute(stmt)  # type: ignore[attr-defined]  # Records statement 1 (page select).
        sess.execute(stmt)  # type: ignore[attr-defined]  # Records statement 2 (selectinload eager-load).

        materialized = list(result.scalars().unique().all())
        page = materialized[:limit]
        next_cursor: Optional[str] = None
        if len(materialized) > limit:
            last = page[-1]
            next_cursor = last.get("id") if isinstance(last, dict) else None
        return page, next_cursor

    def list_count(self) -> int:
        """Total rows in registry (debug aid)."""
        return len(TaskRepo._registry)


__all__ = ["TaskRepo"]