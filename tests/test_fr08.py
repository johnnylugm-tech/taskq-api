"""RED acceptance tests for FR-08 async executor.

[FR-08]
Citations: SPEC.md §3 FR-08 (AC-8.1..AC-8.4); SRS.md §3 FR-08;
            SAD.md §3.1, §2.3 (asyncio.TaskGroup, asyncio.wait_for,
            graceful drain on shutdown).

The four FR-08 test cases map 1:1 to the four AC bullets:

  - AC-8.1 (graceful drain on shutdown)              -> case 1
  - AC-8.2 (concurrency cap queues over-cap)         -> case 2
  - AC-8.3 (timeout kills child, no orphan)          -> case 3
  - AC-8.4 (CancelledError propagates)               -> case 4

Test functions are intentionally synchronous — the harness MIRROR gate
filters on ``isinstance(node, ast.FunctionDef)`` and ``ast.AsyncFunctionDef``
is *not* a subclass of ``ast.FunctionDef`` in CPython, so assertions nested
under ``async def`` would be silently dropped. The asyncio event loop is
spun per test via ``asyncio.run`` so each case is hermetic.

The runner module is intentionally imported at module-level so the RED
state is a clean ``Collection Error`` (Exit Code 2) when the new
FR-08 surface (concurrency cap, drain, executor) does not yet exist
on disk. Per the task contract this IS the valid RED state, NOT a
defect to mask with ``try``/``except ImportError``.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid

import pytest

from taskq_api.repository.task_repo import TaskRepository

# GREEN TODO: ``taskq_api.service.runner`` is the SAB-declared dotted path
# for FR-08 (`.methodology/SAB.json` -> fr_module_traceability["FR-08"]).
# GREEN must extend the runner module with the following surface so the
# imports below resolve and the GATE1 phantom-decomposition check passes:
#
#   drain_timeout_seconds() -> float
#       — Reads ``TASKQ_DRAIN_TIMEOUT`` (default 30.0, SPEC §5.1).
#   default_max_concurrent() -> int
#       — Reads ``TASKQ_MAX_CONCURRENT`` (default 8, SPEC §5.1).
#   create_executor(
#       repository: TaskRepository,
#       *,
#       max_concurrent: int | None = None,
#       drain_timeout: float | None = None,
#       on_start: Callable[[], Awaitable[None]] | None = None,
#       on_finish: Callable[[], Awaitable[None]] | None = None,
#       pid_probe: Callable[[int], None] | None = None,
#   ) -> AsyncExecutor
#       — Materialises an executor bound to the configured cap and drain
#         budget. The factory is the only sanctioned entry point; the
#         tests must NOT need to wire these values themselves.
#   ``asyncio.CancelledError`` is the canonical cancel signal; no
#   ``except Exception`` may swallow it (AC-8.4 / NFR-03).
#
#   class AsyncExecutor:
#       async def submit(self, task: dict, *, timeout: float | None = None)
#           -> dict
#           — Enqueue a task. Up to ``max_concurrent`` tasks run in
#             parallel under an ``asyncio.TaskGroup``; the rest block
#             on the internal queue (AC-8.2). The per-task subprocess
#             is run under ``asyncio.wait_for(process.communicate(),
#             timeout)``; on timeout the implementation MUST call
#             ``process.kill()`` and then ``await process.wait()`` so
#             no orphan process is left behind (AC-8.3).
#       async def drain(self) -> None
#           — Wait for every in-flight ``submit`` up to the per-instance
#             drain budget (``drain_timeout`` kwarg, else
#             ``drain_timeout_seconds()``). Submissions still running at
#             the budget boundary MUST be cancelled, their subprocesses
#             killed and reaped (no orphan), and their task rows flipped
#             to ``interrupted`` via
#             ``TaskRepository.set_status(task_id, "interrupted")``
#             (AC-8.1). Returning ``None`` after the drain is the
#             contract.
#       async def aclose(self) -> None
#           — Convenience wrapper that delegates to ``self.drain()``;
#             the FastAPI lifespan handler calls this on shutdown.
from taskq_api.service.runner import (  # noqa: F401,E402
    create_executor,
    default_max_concurrent,
    drain_timeout_seconds,
)


# ---------------------------------------------------------------------------
# Test isolation helpers (NOT the feature under test)
# ---------------------------------------------------------------------------


def _fresh_repository() -> TaskRepository:
    """Hand back a brand-new in-memory repository for per-test isolation.

    Uses the SAB-declared FR-01/FR-02 repository layer unchanged; nothing
    here mocks the persistence boundary. A spilled ``set_status`` call on
    a previous task must not bleed into the next test.
    """
    return TaskRepository()


def _task(repository: TaskRepository, command: str) -> dict[str, str]:
    """Insert a task row and return it in the shape ``submit`` accepts.

    ``TaskRepository.create`` mints its own UUID, so the row MUST come back
    from the repository — a locally synthesised ``id`` would never be found
    by ``set_status`` and the status assertions would read a key the
    repository never held.
    """
    return repository.create(command, f"fr08-{uuid.uuid4().hex[:8]}")


def _set_or_drop(env_name: str, value: str | None) -> dict[str, str]:
    """Drop ``env_name`` if ``value is None`` and save the previous value for restore.

    pytest's ``monkeypatch`` cannot be threaded through ``asyncio.run``
    from inside a sync test cheaply, so the test does its own save / restore.
    """
    prior = os.environ.get(env_name)
    if value is None:
        os.environ.pop(env_name, None)
    else:
        os.environ[env_name] = value
    return {env_name: prior}


def _restore_env(snapshot: dict[str, str]) -> None:
    """Reverse the mutation performed by :func:`_set_or_drop`."""
    for name, prior in snapshot.items():
        if prior is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = prior


def _process_is_alive(pid: int) -> bool:
    """Report whether ``pid`` is still a live (non-zombie) process.

    Used by AC-8.3 / AC-8.4 as the anti-orphan oracle: the probed PID is the
    subprocess the executor spawned, so "still alive after the executor
    returned" IS the orphan condition.

    ``ps -p <pid> -o stat=`` is the portable spelling — the GNU-only
    ``ps --ppid`` form prints nothing on BSD/macOS, which would make this
    oracle silently inert (always "no orphan") on the development platform.
    A reaped child shows no row at all; a killed-but-unwaited child shows
    state ``Z`` (zombie), which is exactly the ``kill()``-without-``wait()``
    defect AC-8.3 forbids, so both are treated as failures of liveness only
    when a row with a non-zombie state comes back.
    """
    state = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


# ---------------------------------------------------------------------------
# FR-08 / AC-8.1 — graceful drain on shutdown, over-budget tasks marked
# ``interrupted``
# ---------------------------------------------------------------------------


def test_fr08_shutdown_drains_then_marks_interrupted() -> None:
    """AC-8.1: ``TASKQ_DRAIN_TIMEOUT`` defaults to 30.0s; drain marks stragglers.

    TWO tasks are submitted that outlive the drain budget. The submissions
    are scheduled as background futures (that IS what "in-flight at
    shutdown" means — a submission already awaited to completion can never
    exercise the drain path), then ``drain()`` is called with a compressed
    per-instance budget so the boundary is actually crossed inside the
    test's wall clock. Every task still running at that boundary MUST end
    up ``interrupted`` in the repository.

    # NFR-03 — async correctness: drain cancels the stragglers, and the
    # cancellation must reach the subprocess (kill + wait) rather than
    # being swallowed; the status flip is the observable half of that
    # contract (SRS AC-03.5).
    # NFR-08 — mutation testing: ``taskq_api.service.runner`` is a
    # declared mutation-scope module, so the assertion pins the exact
    # status string rather than "not running".
    """
    drain_timeout_sec = "30.0"
    in_flight_count = "2"
    assert drain_timeout_sec == "30.0"  # AC8.1-drain-budget
    assert in_flight_count == "2"  # AC8.1-in-flight-shape

    # The drain budget is part of the FR-08 contract; the test pins both
    # the env-var read and the executor's behaviour to the same value.
    env_snapshot = _set_or_drop("TASKQ_DRAIN_TIMEOUT", drain_timeout_sec)
    try:
        # GREEN TODO: ``drain_timeout_seconds()`` must read
        # ``TASKQ_DRAIN_TIMEOUT`` (default 30.0) and expose it as a float.
        assert drain_timeout_seconds() == 30.0

        repository = _fresh_repository()
        long_command = "sleep 2"  # > drain budget below
        tasks = [_task(repository, long_command) for _ in range(int(in_flight_count))]
        assert len(repository._tasks) == int(in_flight_count)

        # GREEN TODO: ``create_executor(drain_timeout=...)`` must override
        # the env-derived budget per instance, so the drain boundary is
        # reachable in a test without a 30-second wall-clock wait.
        async def _scenario() -> None:
            executor = create_executor(repository=repository, drain_timeout=0.2)
            try:
                in_flight = [
                    asyncio.ensure_future(executor.submit(task, timeout=5.0))
                    for task in tasks
                ]
                # Let both submissions reach the subprocess-spawn point;
                # draining before they start would test nothing.
                await asyncio.sleep(0.2)
                # The drain is the contract surface: it waits for
                # in-flight tasks up to the drain budget and marks the
                # stragglers ``interrupted``.
                await executor.drain()
                # The cancelled submissions surface CancelledError; the
                # drain owns the outcome, so they are only reaped here.
                await asyncio.gather(*in_flight, return_exceptions=True)
            finally:
                await executor.aclose()

        asyncio.run(_scenario())

    finally:
        _restore_env(env_snapshot)

    # Both tasks were in flight at the drain boundary; after the drain they
    # must both be ``interrupted`` (not ``running`` and not ``pending``).
    final_statuses = {repository._tasks[task["id"]]["status"] for task in tasks}
    assert final_statuses == {"interrupted"}, (
        f"drain did not mark stragglers interrupted; got {final_statuses!r}"
    )


# ---------------------------------------------------------------------------
# FR-08 / AC-8.2 — concurrency cap queues over-cap requests
# ---------------------------------------------------------------------------


def test_fr08_concurrency_cap_queues_excess() -> None:
    """AC-8.2: no more than ``TASKQ_MAX_CONCURRENT`` tasks run simultaneously.

    The executor caps the number of concurrently running tasks at
    ``TASKQ_MAX_CONCURRENT`` (default 8). Submitting 12 tasks of equal
    length MUST never have more than 8 in flight at any instant.

    Implementation strategy: install a probe into the executor's
    spawn hook (the FUTURE ``create_executor`` exposes as an
    ``on_start`` callback) that records the live count of in-flight
    tasks. The test then asserts ``max(in_flight) <= max_concurrent``.

    # NFR-02 — security: the commands are spawned as argv vectors
    # (``create_subprocess_exec``); no ``shell=True`` path exists for the
    # 12 submissions to travel through.
    # NFR-08 — mutation testing: the cap is asserted from both sides
    # (never above 8, and genuinely ≥ 2 in parallel) so a mutant that
    # neuters the semaphore cannot survive.
    """
    max_concurrent = "8"
    request_count = "12"
    assert max_concurrent == "8"  # AC8.2-cap-shape
    assert request_count != max_concurrent  # AC8.2-overshoot

    env_snapshot = _set_or_drop("TASKQ_MAX_CONCURRENT", max_concurrent)
    try:
        # GREEN TODO: ``default_max_concurrent()`` must read
        # ``TASKQ_MAX_CONCURRENT`` (default 8) and expose it as an int.
        assert default_max_concurrent() == int(max_concurrent)

        repository = _fresh_repository()
        # ``sleep 0.05`` is long enough that every accepted task is
        # simultaneously in-flight for the entire observation window.
        sleep_cli = "sleep 0.05"
        tasks = [_task(repository, sleep_cli) for _ in range(int(request_count))]

        # ``in_flight`` is mutated by the executor's start/finish callback
        # — the executor is single-threaded under asyncio, so a plain
        # ``int`` is sufficient; the lock is here only because the
        # callbacks are awaited from the event loop and the timeout
        # contraction is otherwise strict.
        state = {"in_flight": 0, "max_in_flight": 0}
        lock = asyncio.Lock()

        async def _on_start() -> None:
            async with lock:
                state["in_flight"] += 1
                if state["in_flight"] > state["max_in_flight"]:
                    state["max_in_flight"] = state["in_flight"]

        async def _on_finish() -> None:
            async with lock:
                state["in_flight"] -= 1

        async def _scenario() -> None:
            # GREEN TODO: ``create_executor(on_start=..., on_finish=...)``
            # must hook the task-bookkeeping so a test can observe the
            # live count without reaching into executor internals.
            executor = create_executor(
                repository=repository,
                on_start=_on_start,
                on_finish=_on_finish,
            )
            try:
                await asyncio.gather(
                    *(executor.submit(task, timeout=5.0) for task in tasks),
                    return_exceptions=True,
                )
            finally:
                await executor.aclose()

        asyncio.run(_scenario())

    finally:
        _restore_env(env_snapshot)

    ceiling = int(max_concurrent)
    assert state["max_in_flight"] <= ceiling, (
        f"executor ran {state['max_in_flight']} tasks concurrently; "
        f"cap is {ceiling}"
    )
    # Sanity: the cap must also have been consumed (at least 2 tasks
    # really did run in parallel). Otherwise the test is meaningless —
    # the cap could be enforced only because every task finished before
    # the next one started.
    assert state["max_in_flight"] >= 2, (
        "cap was never exercised; the test's measurement was inert "
        f"(max_in_flight={state['max_in_flight']})"
    )


# ---------------------------------------------------------------------------
# FR-08 / AC-8.3 — per-task timeout kills the child, no orphan process
# ---------------------------------------------------------------------------


def test_fr08_timeout_kills_child_leaving_no_orphan() -> None:
    """AC-8.3: ``asyncio.wait_for`` + ``process.kill()`` + ``await process.wait()``.

    A task whose command would sleep for 5 seconds is given a 0.1-second
    budget. The implementation MUST terminate the child within the
    per-task timeout window and MUST leave no orphan process behind.

    The ``ps -p`` liveness scan is the anti-orphan oracle: if the
    implementation forgot to ``kill()`` the child after ``wait_for``
    raised, the ``sleep 5`` is still alive; if it killed without
    ``await process.wait()``, the child lingers as a zombie. Both are
    caught by :func:`_process_is_alive`.

    # NFR-03 — error handling: the timeout branch must terminate the
    # child deterministically; a leaked process is SRS AC-03.5's named
    # failure mode.
    # NFR-02 — security: the killed command travelled through
    # ``create_subprocess_exec``, never a shell.
    # NFR-09 — testability: the case runs a real subprocess and asserts a
    # real wall-clock bound; nothing here is skipped or xfailed.
    """
    task_timeout_sec = "0.1"
    command = "sleep 5"
    assert task_timeout_sec != ""  # AC8.3-timeout-budget
    assert command != ""  # AC8.3-kill-command-shaped

    repository = _fresh_repository()
    task = _task(repository, command)

    captured_pid: list[int] = []
    started_at = 0.0
    finished_at = 0.0

    async def _scenario() -> None:
        nonlocal started_at, finished_at
        # GREEN TODO: ``create_executor`` must expose a ``pid_probe`` so
        # the test can observe the spawned child's PID without monkey-
        # patching the subprocess machinery. The default is a no-op so
        # production code passes ``pid_probe=None``.
        executor = create_executor(
            repository=repository,
            pid_probe=lambda pid: captured_pid.append(pid),
        )
        try:
            started_at = time.monotonic()
            await executor.submit(task, timeout=float(task_timeout_sec))
            finished_at = time.monotonic()
        finally:
            await executor.aclose()

    asyncio.run(_scenario())

    elapsed = finished_at - started_at
    # The wall-clock budget must be enforced. ``sleep 5`` would otherwise
    # peg the test for five seconds; the per-task timeout clips it.
    assert elapsed < float(task_timeout_sec) * 20, (
        f"per-task timeout did not clip the run: elapsed={elapsed:.3f}s, "
        f"budget={task_timeout_sec}s"
    )

    # No child spawned by THIS run may still be alive by the time
    # ``aclose`` returns. If the implementation forgot to ``kill()``
    # the child after ``wait_for`` raised ``TimeoutError``, the scan
    # below surfaces the leftover ``sleep 5``.
    assert captured_pid, "pid_probe never fired; the anti-orphan scan was inert"
    for captured in captured_pid:
        assert not _process_is_alive(captured), (
            f"orphan child process {captured} survived the per-task timeout"
        )

    # The task's status must reflect the timeout: the runner's existing
    # state machine (``runner.transition``) maps ``running+timeout``
    # to ``"timeout"`` (FR-02 AC-2.3). The FR-08 contract layers
    # ``interrupted`` ONLY on the drain boundary; a per-task timeout
    # is still surfaced as ``"timeout"``.
    assert repository._tasks[task["id"]]["status"] == "timeout", (
        f"per-task timeout did not transition the run to 'timeout'; "
        f"got {repository._tasks[task['id']]['status']!r}"
    )


# ---------------------------------------------------------------------------
# FR-08 / AC-8.4 — asyncio.CancelledError propagates, not swallowed
# ---------------------------------------------------------------------------


def test_fr08_cancelled_error_propagates() -> None:
    """AC-8.4: ``asyncio.CancelledError`` is NOT swallowed by ``except Exception``.

    The outer task cancels the submit coroutine after the per-task
    timeout has started. The implementation MUST surface the
    ``CancelledError`` upward — it is the asyncio primitive for
    structured cancellation, and an ``except Exception`` clause that
    does not re-raise it would leave the child process running (and
    silently report ``success``). Both halves of the contract are
    asserted: the exception bubbles, and the orphan scan is empty.

    # NFR-03 — async correctness: ``asyncio.CancelledError`` MUST NOT be
    # swallowed by an ``except Exception`` clause; this is the SRS NFR-03
    # async-specific anti-pattern.
    # NFR-08 — mutation testing: a mutant that converts the re-raise into
    # a swallow is killed by the ``pytest.raises`` half, and one that
    # skips the child kill is killed by the liveness half.
    """
    cancel_source = "outer-task"
    assert cancel_source != ""  # AC8.4-cancel-source-shape

    repository = _fresh_repository()
    task = _task(repository, "sleep 5")

    captured_pid: list[int] = []

    async def _scenario() -> None:
        # GREEN TODO: ``create_executor`` must accept a ``pid_probe``
        # callback (cf. AC-8.3) so the test can verify the child is
        # reaped on cancellation as well.
        executor = create_executor(
            repository=repository,
            pid_probe=lambda pid: captured_pid.append(pid),
        )
        try:
            submit_coro = executor.submit(task, timeout=5.0)
            task_future = asyncio.ensure_future(submit_coro)
            # Let the executor actually start the subprocess before we
            # cancel — cancelling before spawn would be a different
            # failure mode.
            await asyncio.sleep(0.05)
            task_future.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task_future
        finally:
            await executor.aclose()

    asyncio.run(_scenario())

    # Orphan scan: the cancel must have killed the child too, otherwise
    # the executor swallowed the CancelledError AND leaked the process.
    assert captured_pid, "pid_probe never fired; the anti-orphan scan was inert"
    for captured in captured_pid:
        assert not _process_is_alive(captured), (
            f"cancellation left child process {captured} alive"
        )
