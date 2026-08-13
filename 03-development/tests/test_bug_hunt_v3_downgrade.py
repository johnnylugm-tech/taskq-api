"""Bug-hunt repro: v3_split_results.downgrade() rebuilds tasks.result_json
with `ORDER BY tr.rowid DESC LIMIT 1` — only the newest task_results row
per task is preserved. All earlier runs are lost.

RED proof: apply the downgrade's _REPOPULATE_RESULT_JSON SQL (taken
directly from the migration module) on a fresh SQLite, then assert every
run is preserved.
"""
from __future__ import annotations

import json
import sqlite3

from migrations.versions.v3_split_results import _REPOPULATE_RESULT_JSON


def _setup_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                name TEXT,
                command TEXT,
                status TEXT
            );
            CREATE TABLE task_results (
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
        for tid in ("t1", "t2"):
            conn.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?)",
                (tid, f"name-{tid}", "echo", "pending"),
            )
        for tid in ("t1", "t2"):
            for rid in ("r1", "r2", "r3"):
                conn.execute(
                    "INSERT INTO task_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"{tid}-{rid}", tid, rid, 0, "out", "err", 1, "now", "done"),
                )
        conn.execute("ALTER TABLE tasks ADD COLUMN result_json TEXT")
        conn.commit()
    finally:
        conn.close()


def test_v3_downgrade_preserves_all_runs(tmp_path):
    db = tmp_path / "downgrade.sqlite"
    _setup_db(str(db))

    conn = sqlite3.connect(str(db))
    try:
        conn.execute(_REPOPULATE_RESULT_JSON)
        conn.commit()
        rows = conn.execute("SELECT id, result_json FROM tasks").fetchall()
        repop = {tid: json.loads(rj) for tid, rj in rows if rj}
    finally:
        conn.close()

    for tid in ("t1", "t2"):
        rec = repop.get(tid)
        assert rec is not None, f"task {tid} has no result_json after downgrade"
        # After fix: a task with 3 runs must surface all 3 runs in a `runs` array.
        runs = rec.get("runs") if isinstance(rec, dict) else None
        assert isinstance(runs, list) and len(runs) == 3, (
            f"task {tid}: downgrade lost runs; got {rec!r}"
        )
        run_ids = sorted(r["run_id"] for r in runs)
        assert run_ids == ["r1", "r2", "r3"], run_ids
