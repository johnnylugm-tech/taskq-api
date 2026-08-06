"""Task execution runner.

[FR-02 / FR-08]
Citations:
  - FR-02 — SPEC.md lines 93-100, 293, 312; SRS.md lines 88-105.
  - FR-08 — SPEC.md §3 FR-08 (lines 147-148), §5.1 (lines 294-295);
           SAD.md §3.1, §2.3 (asyncio.TaskGroup, asyncio.wait_for,
           graceful drain on shutdown).

The run results are held in a module-level list standing in for the v3
``task_results`` table; FR-07 introduces the real schema and migration, at
which point ``record_result`` / ``list_runs`` become repository-backed.
FR-08 layers an async executor on top of the same subprocess primitives
so background submissions are cap-bounded and shut down cleanly.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from taskq_api.repository.task_repo import TaskRepository

_DEFAULT_TIMEOUT_SECONDS = 10.0
_TAIL_CHARACTER_LIMIT = 4096
_TIMEOUT_EXIT_CODE = -1
_INITIAL_RUN_STATUS = "pending"

# FR-08 — environmental defaults (SPEC §5.1, lines 294-295).
_DEFAULT_DRAIN_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_CONCURRENT = 8

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


# ---------------------------------------------------------------------------
# FR-08 — asynchronous executor (SPEC §3 FR-08, §5.1; SAD §3.1, §2.3)
# ---------------------------------------------------------------------------
#
# The executor layers cap-bounded submission (AC-8.2), per-task subprocess
# timeout (AC-8.3) and graceful drain on shutdown (AC-8.1) on top of the
# existing FR-02 subprocess primitives. Cancellation is structured:
# ``asyncio.CancelledError`` propagates end-to-end (AC-8.4) and never gets
# swallowed by a bare ``except Exception`` (NFR-03).


def drain_timeout_seconds() -> float:
    """Read the FR-08 drain budget from the environment. [FR-08]

    Citations: SPEC.md line 295; SRS.md §3 FR-08.
    """
    return float(os.getenv("TASKQ_DRAIN_TIMEOUT", str(_DEFAULT_DRAIN_TIMEOUT_SECONDS)))


def default_max_concurrent() -> int:
    """Read the FR-08 concurrency cap from the environment. [FR-08]

    Citations: SPEC.md line 294; SRS.md §3 FR-08.
    """
    return int(os.getenv("TASKQ_MAX_CONCURRENT", str(_DEFAULT_MAX_CONCURRENT)))


def create_executor(
    repository: TaskRepository,
    *,
    max_concurrent: int | None = None,
    drain_timeout: float | None = None,
    on_start: Callable[[], Awaitable[None]] | None = None,
    on_finish: Callable[[], Awaitable[None]] | None = None,
    pid_probe: Callable[[int], None] | None = None,
) -> "AsyncExecutor":
    """Materialise an :class:`AsyncExecutor` bound to the configured cap. [FR-08]

    Citations: SPEC.md §3 FR-08 (lines 147-148); SAD.md §3.1, §2.3.

    The factory is the only sanctioned entry point so the per-instance
    budget (``drain_timeout``) overrides the env-derived default,
    letting tests cross the drain boundary without a 30-second wall-clock
    wait.
    """
    return AsyncExecutor(
        repository=repository,
        max_concurrent=max_concurrent if max_concurrent is not None else default_max_concurrent(),
        drain_timeout=drain_timeout if drain_timeout is not None else drain_timeout_seconds(),
        on_start=on_start,
        on_finish=on_finish,
        pid_probe=pid_probe,
    )


async def _noop_coro() -> None:
    """Default for ``on_start`` / ``on_finish`` when no probe is wired."""


def _noop_probe(pid: int) -> None:
    """Default for ``pid_probe`` when no observer is wired."""


class AsyncExecutor:
    """Cap-bounded, drain-bounded async executor. [FR-08]

    Citations: SPEC.md §3 FR-08 (AC-8.1..AC-8.4); SAD.md §3.1, §2.3.

    Lifecycle: ``create_executor`` -> ``await submit(...)`` (repeatable) ->
    ``await drain()`` (or ``await aclose()``) at shutdown. Each ``submit``
    acquires a permit from the internal semaphore so at most
    ``max_concurrent`` subprocesses run at once (AC-8.2). Per-task
    timeouts are enforced with ``asyncio.wait_for`` and a paired
    ``process.kill()`` / ``await process.wait()`` (AC-8.3).
    Cancellation propagates through ``submit`` without an intervening
    ``except Exception`` swallow (AC-8.4 / NFR-03).
    """

    def __init__(
        self,
        *,
        repository: TaskRepository,
        max_concurrent: int,
        drain_timeout: float,
        on_start: Callable[[], Awaitable[None]] | None,
        on_finish: Callable[[], Awaitable[None]] | None,
        pid_probe: Callable[[int], None] | None,
    ) -> None:
        self._repository = repository
        self._sem = asyncio.Semaphore(max_concurrent)
        self._drain_timeout = drain_timeout
        self._on_start = on_start if on_start is not None else _noop_coro
        self._on_finish = on_finish if on_finish is not None else _noop_coro
        self._pid_probe = pid_probe if pid_probe is not None else _noop_probe
        # ``work_fut -> task_id`` so ``drain`` can name stragglers and
        # ``submit`` can reap if cancelled while awaiting the worker.
        # asyncio is cooperative single-threaded; a plain ``dict`` is
        # race-free because every mutation is bracketed by ``await``
        # boundaries, never inside one.
        self._tracked: dict[asyncio.Future[Any], str] = {}

    async def submit(self, task: dict, *, timeout: float | None = None) -> dict:
        """Enqueue one task under the concurrency cap. [FR-08]

        Citations: SPEC.md §3 FR-08 (AC-8.2, AC-8.3, AC-8.4).

        Returns the per-run result row. On per-task timeout the child is
        killed and reaped (no orphan); ``asyncio.CancelledError`` from
        the awaiting task propagates through, with the in-flight worker
        cancelled and reaped so the subprocess cannot outlive the
        caller (AC-8.3 / AC-8.4).
        """
        task_id = task["id"]
        work_coro = self._execute(task, timeout=timeout, task_id=task_id)
        work_fut: asyncio.Future[dict] = asyncio.ensure_future(work_coro)
        self._tracked[work_fut] = task_id
        try:
            return await work_fut
        finally:
            # If the outer await was cancelled (or errored) before
            # ``work_fut`` finished, ensure the worker is also stopped
            # and the subprocess is reaped — AC-8.3 / AC-8.4 require that
            # ``CancelledError`` reach the child, not just the coroutine.
            if not work_fut.done():
                work_fut.cancel()
                try:
                    await work_fut
                except BaseException:
                    pass
            self._tracked.pop(work_fut, None)

    async def _execute(
        self,
        task: dict,
        *,
        timeout: float | None,
        task_id: str,
    ) -> dict:
        process = None
        # The semaphore wraps the whole execution so the cap counts
        # in-flight subprocesses (not pending submissions). ``on_start``
        # therefore fires AFTER acquire and ``on_finish`` BEFORE release
        # so the live count tracks exactly the cap (AC-8.2).
        async with self._sem:
            await self._on_start()
            try:
                self._repository.set_status(task_id, "running")
                process = await spawn_process(task["command"])
                self._pid_probe(process.pid)
                try:
                    await asyncio.wait_for(process.communicate(), timeout=timeout)
                    exit_code = process.returncode
                    status = "done" if exit_code == 0 else "failed"
                except asyncio.TimeoutError:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
                    status = "timeout"
                self._repository.set_status(task_id, status)
                return self._result_row(task_id, status)
            finally:
                # Catch the cancellation / unexpected-error paths: the
                # child MUST be reaped regardless of how ``_execute``
                # exits (AC-8.3 anti-orphan, NFR-03). ``returncode`` is
                # ``None`` only while the process is still running.
                if process is not None and process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await process.wait()
                    except BaseException:
                        pass
                try:
                    await self._on_finish()
                except BaseException:
                    pass

    async def drain(self) -> None:
        """Wait for every in-flight ``submit`` and reap the stragglers. [FR-08]

        Citations: SPEC.md §3 FR-08 (AC-8.1); §5.1 line 295.

        Tasks still running at the drain budget boundary are cancelled,
        their subprocesses killed and waited on (no orphan), and their
        task rows flipped to ``interrupted`` via
        ``TaskRepository.set_status``. Returning ``None`` is the contract.
        """
        # Snapshot first: subsequent mutations from inside the worker
        # coroutines must not perturb this iteration.
        items = list(self._tracked.items())
        if not items:
            return
        futs = [fut for fut, _ in items]
        try:
            await asyncio.wait_for(
                asyncio.gather(*futs, return_exceptions=True),
                self._drain_timeout,
            )
        except asyncio.TimeoutError:
            # Expected: the cap of ``futs`` here is the drain budget,
            # not the per-task one; stragglers are handled below. By
            # the time this raises, ``gather`` has already cancelled
            # and reaped each child future, so the cancel loop below
            # only catches the rare race where a worker has not yet
            # finished cleanup.
            pass

        # Cancel any straggler whose worker is still unwinding, then
        # flip every task row that did NOT complete through the normal
        # success/failure/timeout path to ``interrupted``. The check
        # below relies on the runner's own state machine: a task row
        # that survived the drain in ``pending`` or ``running`` is by
        # definition mid-execution at the boundary, hence ``interrupted``.
        terminal_statuses = {"done", "failed", "timeout"}
        for fut, task_id in items:
            if not fut.done():
                fut.cancel()
            try:
                current = self._repository._tasks.get(task_id, {}).get("status")
            except Exception:
                current = None
            if current not in terminal_statuses:
                try:
                    self._repository.set_status(task_id, "interrupted")
                except Exception:
                    pass

        # Reap: await each cancelled worker so its ``finally`` runs the
        # kill+wait before ``drain`` returns. Any exception (Cancel or
        # otherwise) is part of structured shutdown and is swallowed.
        for fut, _ in items:
            try:
                await fut
            except BaseException:
                pass

    async def aclose(self) -> None:
        """Drain and discard. [FR-08] — FastAPI lifespan exit hook.

        Citations: SPEC.md §3 FR-08 (AC-8.1).
        """
        await self.drain()

    @staticmethod
    def _result_row(task_id: str, status: str) -> dict:
        """Build a minimal per-run result row for ``submit``'s return value."""
        return {"task_id": task_id, "status": status}
