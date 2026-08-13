"""Bug-hunt repro: v3_split_results.upgrade() swallows SQLAlchemyError
from the backfill and then unconditionally drops tasks.result_json. On
any backfill failure, the original data is lost with no trace.

RED proof: feed upgrade() a bind whose execute raises on the backfill
INSERT and assert (a) upgrade raises (or the column is preserved) and
(b) drop_column is NOT called.
"""
from __future__ import annotations

import sqlite3

import pytest


class _ExplodingBind:
    """Wraps a sqlite3.Connection; raises SQLAlchemyError on the backfill."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, *args, **kwargs):
        sql_obj = args[0] if args else kwargs.get("sql", "")
        # sa.text() wraps the string into a TextClause; render to str.
        sql = str(sql_obj)
        if "INSERT INTO task_results" in sql:
            from sqlalchemy.exc import SQLAlchemyError
            raise SQLAlchemyError("simulated backfill failure")
        return self._conn.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_v3_upgrade_does_not_drop_on_backfill_failure(tmp_path, monkeypatch):
    db = tmp_path / "upgrade_fail.sqlite"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                name TEXT,
                command TEXT,
                result_json TEXT,
                status TEXT
            );
            CREATE TABLE api_keys (
                id TEXT PRIMARY KEY,
                scope TEXT,
                key_hash TEXT,
                revoked_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO tasks VALUES ('t1', 'n1', 'echo', "
            "'{\"id\":\"r1\",\"run_id\":\"r1\",\"exit_code\":0}', 'pending')"
        )
        conn.commit()
    finally:
        conn.close()

    bind_conn = sqlite3.connect(str(db))
    # Pre-create task_results so create_table is a no-op.
    bind_conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS task_results (
            id TEXT,
            task_id TEXT,
            run_id TEXT,
            exit_code INTEGER,
            stdout_tail TEXT,
            stderr_tail TEXT,
            duration_ms INTEGER,
            finished_at TEXT,
            status TEXT
        );
        """
    )
    bind_conn.commit()

    bind = _ExplodingBind(bind_conn)

    from alembic import op as _op

    captured: dict = {"dropped": False, "created": False}

    class _Batch:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def drop_column(self, *_a, **_k):
            captured["dropped"] = True

    class _Ops:
        def create_table(self, *a, **k):
            captured["created"] = True
        def batch_alter_table(self, *a, **k):
            return _Batch()

    monkeypatch.setattr(_op, "get_bind", lambda: bind)
    monkeypatch.setattr(_op, "create_table", lambda *a, **k: captured.__setitem__("created", True))
    monkeypatch.setattr(_op, "batch_alter_table", lambda *_a, **_k: _Batch())

    from migrations.versions.v3_split_results import upgrade

    with pytest.raises(Exception):
        upgrade()

    assert captured["dropped"] is False, (
        "upgrade() called drop_column despite backfill failure"
    )

    # result_json column must still exist.
    conn = sqlite3.connect(str(db))
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
        assert "result_json" in cols, "result_json column was dropped on failed upgrade"
    finally:
        conn.close()
