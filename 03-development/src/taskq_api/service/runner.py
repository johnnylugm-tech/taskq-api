"""Task execution runner.

[FR-02]
Citations: SPEC.md lines 93-100, 293, 312; SRS.md lines 88-105.

The run results are held in a module-level list standing in for the v3
``task_results`` table; FR-07 introduces the real schema and migration, at
which point ``record_result`` / ``list_runs`` become repository-backed.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from taskq_api.repository.task_repo import TaskRepository

_DEFAULT_TIMEOUT_SECONDS = 10.0
_TAIL_CHARACTER_LIMIT = 4096
_TIMEOUT_EXIT_CODE = -1
_INITIAL_RUN_STATUS = "pending"

# pending → running → done | failed | timeout (SPEC.md line 97).
_TRANSITIONS: dict[tuple[str, str], str] = {
    ("pending", "start"): "running",
    ("running", "success"): "done",
    ("running", "exit_nonzero"): "failed",
    ("running", "timeout"): "timeout",
}

_task_results: list[dict[str, Any]] = []


def task_timeout_seconds() -> float:
    """Read the per-run subprocess timeout from the environment. [FR-02]

    Citations: SPEC.md lines 96, 293.
    """
    return float(os.getenv("TASKQ_TASK_TIMEOUT", str(_DEFAULT_TIMEOUT_SECONDS)))


async def spawn_process(command: str) -> Any:
    """Spawn a task's command as an argv vector, never through a shell. [FR-02]

    Citations: SPEC.md lines 72, 96; SRS.md lines 96-97.

    Shell interpretation is forbidden (NFR-02 / SEC T-01), so the command
    string is split with ``shlex`` and handed to ``create_subprocess_exec``
    as separate arguments -- no shell ever sees the string, so it cannot
    reinterpret metacharacters as separators, redirections or expansions.
    """
    return await asyncio.create_subprocess_exec(
        *shlex.split(command),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


def transition(status: str, event: str) -> str:
    """Advance the run state machine by one event. [FR-02]

    Citations: SPEC.md line 97; SRS.md lines 98-99.
    """
    try:
        return _TRANSITIONS[(status, event)]
    except KeyError:
        raise ValueError(f"illegal transition: {status!r} + {event!r}") from None


def record_result(
    task_id: str,
    exit_code: int,
    stdout_tail: str,
    stderr_tail: str,
    duration_ms: int,
    finished_at: str,
) -> dict[str, Any]:
    """Persist one execution result row and return it. [FR-02]

    Citations: SPEC.md lines 98, 312; SRS.md lines 100-103.
    """
    row: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "task_id": task_id,
        "exit_code": exit_code,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "duration_ms": duration_ms,
        "finished_at": finished_at,
    }
    _task_results.append(row)
    return row.copy()


def list_runs(task_id: str) -> list[dict[str, Any]]:
    """Return one task's execution history, newest first. [FR-02]

    Citations: SPEC.md line 99; SRS.md lines 104-105.
    """
    return [row.copy() for row in reversed(_task_results) if row["task_id"] == task_id]


async def execute_task(task: dict[str, str], repository: TaskRepository) -> dict[str, Any]:
    """Run one task to completion and record its result row. [FR-02]

    Citations: SPEC.md lines 96-98; SRS.md lines 96-103.

    The ``pending → running → done | failed | timeout`` lifecycle belongs to
    each *run*, not to the task row -- a task is runnable any number of times
    (AC-2.5), so every run starts from ``pending``. The task row mirrors the
    status of its latest run. Every status change goes through
    :func:`transition`, keeping the state machine the single mutation point
    for the run lifecycle (NFR-03).
    """
    task_id = task["id"]
    status = transition(_INITIAL_RUN_STATUS, "start")
    repository.set_status(task_id, status)

    started_at = time.monotonic()
    process = await spawn_process(task["command"])
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), task_timeout_seconds()
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        stdout, stderr = b"", b""
        exit_code = _TIMEOUT_EXIT_CODE
        status = transition(status, "timeout")
    else:
        exit_code = await process.wait()
        status = transition(status, "success" if exit_code == 0 else "exit_nonzero")

    duration_ms = int((time.monotonic() - started_at) * 1000)
    repository.set_status(task_id, status)
    return record_result(
        task_id=task_id,
        exit_code=exit_code,
        stdout_tail=_tail(stdout),
        stderr_tail=_tail(stderr),
        duration_ms=duration_ms,
        finished_at=_utc_now_iso(),
    )


def _tail(stream: bytes) -> str:
    """Decode a captured stream and keep only its bounded tail."""
    return stream.decode("utf-8", errors="replace")[-_TAIL_CHARACTER_LIMIT:]


def _utc_now_iso() -> str:
    """Stamp an RFC 3339 UTC instant that sorts lexicographically."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
