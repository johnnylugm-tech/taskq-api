"""Bug-hunt repro: TaskRepo.list accepts a cursor but never applies it
to the SELECT. A client requesting page 2 with ?cursor=<id> receives
the same first page — pagination is broken.

RED proof: capture the stmt TaskRepo.list forwards to session.execute
and assert the cursor value is bound into the WHERE clause.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _capture_stmt(monkeypatch):
    """Replace session.get_session with a stub that captures the stmt."""
    captured: dict = {"stmt": None}

    class _FakeResult:
        def scalars(self): return self
        def unique(self): return self
        def all(self): return []

    class _FakeSession:
        def add(self, r): pass
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass
        def query(self, *a, **k): return self
        def filter(self, *a, **k): return self
        def all(self): return []
        def first(self): return None

        def execute(self, stmt, *a, **k):
            captured["stmt"] = stmt
            return _FakeResult()

    from taskq_api.repository import session as _session_module
    monkeypatch.setattr(_session_module, "get_session", lambda: _FakeSession())

    return captured


def test_cursor_is_bound_into_where_clause(_capture_stmt):
    from taskq_api.repository.task_repo import TaskRepo

    TaskRepo._registry.clear()
    TaskRepo._by_name.clear()
    TaskRepo._registry["id-A"] = {"id": "id-A", "name": "A", "status": "pending"}
    TaskRepo._registry["id-B"] = {"id": "id-B", "name": "B", "status": "pending"}

    repo = TaskRepo(session=None)
    repo.list(cursor="id-A", limit=10)

    stmt = _capture_stmt["stmt"]
    assert stmt is not None, "TaskRepo.list never called session.execute"

    # Inspect the WHERE clause for the cursor predicate.
    where = getattr(stmt, "_whereclause", None)
    assert where is not None, "stmt has no WHERE clause — cursor was dropped"

    where_str = str(where)
    assert ">" in where_str, (
        f"cursor filter must use strict-greater-than; got {where_str!r}"
    )

    # SQLAlchemy parameterises the cursor into a bound parameter; confirm
    # the rendered (literal-binds) SQL carries the cursor value.
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "id-A" in compiled, (
        f"cursor value missing from compiled SQL; got {compiled!r}"
    )
