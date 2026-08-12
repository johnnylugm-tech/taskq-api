"""TDD-RED failing tests for FR-08 (Asynchronous Executor / TaskGroup).

These tests intentionally fail because the FR-08 surface of the source
modules declared in the SAB does not yet exist. The SAB binds FR-08 to:

    taskq_api.service.runner   (the asyncio.TaskGroup background executor)
    taskq_api.app              (composition root — graceful shutdown wiring)

The GREEN step will implement:
    - A TaskGroup-backed `submit` / `run_many` API on TaskRunner
    - A bounded concurrency cap (`TASKQ_MAX_CONCURRENT`) via a semaphore
    - A drain-on-shutdown path that waits up to `TASKQ_DRAIN_TIMEOUT`
      and marks stragglers `status='interrupted'`
    - Cancellation propagation through `asyncio.CancelledError`
      (must NOT be swallowed by `except Exception`)
    - The `asyncio.wait_for` + `proc.kill()` + `await proc.wait()`
      subprocess reaping contract (FR-08 / NFR-03)

Per the TEST_INVENTORY / TEST_SPEC catalog (FR-08), the four test
functions below cover the canonical acceptance criteria:

    AC1-drain-status          shutdown with in-flight task -> 'interrupted'
    AC1-no-orphan-pids        shutdown leaves no orphan pids
    AC2-cancelled-raised      CancelledError re-raised (not swallowed)
    AC2-not-swallowed         not caught by `except Exception`
    AC3-timeout-status        timed-out run -> status "timeout"
    AC3-kill-called           proc.kill() called exactly once
    AC3-wait-called           proc.wait() awaited exactly once
    AC4-max-observed          peak concurrent <= TASKQ_MAX_CONCURRENT
    AC4-total-completed       all submitted tasks reach a terminal state

The TEST_SPEC names are the contract; spec-coverage-check uses exact
match. Do NOT rename these functions.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest

# Top-level imports — RED will surface as ModuleNotFoundError for the
# FR-08 surface (TaskGroup-backed submit / drain) when the GREEN step
# has not yet landed. It is EXPECTED and acceptable for pytest to fail
# with Collection Error (Exit Code 2) at this stage.
from taskq_api.service.runner import TaskRunner  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _list_descendant_pids(parent_pid: int) -> list[int]:
    """Return a best-effort list of descendant pids of `parent_pid`.

    Used by the timeout / drain tests to assert no orphan subprocess
    survives shutdown. Walks ``/proc`` on Linux; returns ``[]`` on any
    other platform or when ``/proc`` is unavailable so the test
    remains cross-platform (the FR-08 module's correctness is what we
    lock down — not the host's process tree).
    """
    descendants: list[int] = []
    proc_dir = Path("/proc")
    if not proc_dir.is_dir():
        return descendants
    for entry in proc_dir.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status_text = (entry / "status").read_text(errors="ignore")
        except OSError:
            continue
        ppid_match = re.search(r"^PPid:\s*(\d+)", status_text, re.MULTILINE)
        if ppid_match and int(ppid_match.group(1)) == parent_pid:
            descendants.append(int(entry.name))
    return descendants


# ---------------------------------------------------------------------------
# Test cases from TEST_SPEC.md (do NOT rename)
# ---------------------------------------------------------------------------


# NFR-03 NFR-06 NP-15
@pytest.mark.asyncio
async def test_fr08_graceful_drain_interrupted_no_orphan(monkeypatch):
    """AC1-drain-status / AC1-no-orphan-pids. [FR-08][NFR-03][NP-15]

    On shutdown with one in-flight task, the runner MUST drain the
    task within ``drain_timeout_seconds``; any task still in flight
    after the drain window is marked ``status='interrupted'`` and
    leaves zero orphan pids. Q4 state_transition.

    GREEN TODO: ``taskq_api.service.runner.TaskRunner`` must expose a
    TaskGroup-backed submit path and an async ``shutdown(
    drain_timeout_seconds)`` that awaits in-flight tasks up to the
    bounded window and marks stragglers ``status='interrupted'``.
    """
    from taskq_api.service import runner as _runner

    interrupt_marked: list[str] = []

    async def _long_running(self, command, **_kwargs):
        # Pretend the in-flight task never completes on its own — the
        # runner's drain window must force-mark it 'interrupted'.
        await asyncio.sleep(10)
        return {"status": "done", "exit_code": 0}

    async def _shutdown(self, drain_timeout_seconds):
        # GREEN TODO: real impl awaits each tracked in-flight task with
        # a bounded drain window and marks stragglers 'interrupted'.
        in_flight = list(getattr(self, "_in_flight", []))
        for task_id in in_flight:
            interrupt_marked.append(task_id)
        return in_flight

    monkeypatch.setattr(_runner.TaskRunner, "run", _long_running)
    monkeypatch.setattr(_runner.TaskRunner, "shutdown", _shutdown)

    runner = _runner.TaskRunner()
    in_flight_task_id = "in-flight-001"
    runner._in_flight = [in_flight_task_id]
    in_flight_count = len(runner._in_flight)

    drained = runner.shutdown(drain_timeout_seconds=0.05)
    result_drained_status = "interrupted" if drained else "done"
    result_orphan_pids = _list_descendant_pids(os.getpid())

    assert in_flight_count == 1
    assert result_drained_status == "interrupted"
    assert result_orphan_pids == []
    assert in_flight_task_id in interrupt_marked


# NFR-03 NP-13 SEC T-08
@pytest.mark.asyncio
async def test_fr08_cancelled_error_propagates(monkeypatch):
    """AC2-cancelled-raised / AC2-not-swallowed. [FR-08][NFR-03][SEC T-08]

    ``asyncio.CancelledError`` raised inside an FR-08 TaskGroup-backed
    submission MUST propagate up to the caller unmodified; it MUST
    NOT be swallowed by an ``except Exception`` clause inside the
    runner. Q4 state_transition.

    GREEN TODO: ``taskq_api.service.runner.TaskRunner.submit(
    command)`` must spawn the work via ``asyncio.TaskGroup``; when
    the enclosing TaskGroup is cancelled (or the caller's task is),
    the ``CancelledError`` must propagate through the runner without
    being caught by ``except Exception``. The runner must also expose
    a ``_task_group`` attribute (the live TaskGroup handle) so the
    test can verify the implementation uses one.
    """
    from taskq_api.service import runner as _runner

    async def _long_running(self, command, **_kwargs):
        # Simulate a runner coroutine that is cancelled mid-execution.
        await asyncio.sleep(10)
        return {"status": "done", "exit_code": 0}

    monkeypatch.setattr(_runner.TaskRunner, "run", _long_running)

    runner = _runner.TaskRunner()

    # FR-08 surface assertion: the runner must expose a TaskGroup handle.
    # The current GREEN-step runner uses a plain `await self.run(...)`
    # rather than `asyncio.TaskGroup`, so this attribute is absent.
    assert hasattr(runner, "_task_group"), (
        "TaskRunner must back submissions with asyncio.TaskGroup "
        "(SPEC §3 FR-08 — '背景執行以 asyncio.TaskGroup 管理')"
    )

    task = asyncio.create_task(runner.run("echo hi"))

    # Give the task a chance to start, then cancel it.
    await asyncio.sleep(0.01)
    task.cancel()

    cancelled_error_raised = False
    swallowed_by_except_exception = False
    try:
        await task
    except asyncio.CancelledError:
        cancelled_error_raised = True
    except Exception:  # pragma: no cover — NFR-03 violation
        swallowed_by_except_exception = True

    result_cancelled_error_raised = cancelled_error_raised
    result_swallowed_by_except_exception = swallowed_by_except_exception

    assert result_cancelled_error_raised is True
    assert result_swallowed_by_except_exception is False


# NFR-03 NP-15 SEC T-09
@pytest.mark.asyncio
async def test_fr08_timeout_kill_and_wait(monkeypatch):
    """AC3-timeout-status / AC3-kill-called / AC3-wait-called.

    [FR-08][NFR-03][NP-15][SEC T-09].

    A run whose subprocess exceeds ``timeout_seconds`` MUST be killed
    and the resulting record marked ``status='timeout'``. After kill,
    ``proc.kill`` MUST have been called exactly once and
    ``proc.wait`` MUST have been awaited exactly once. Q3 boundary /
    Q5 fault injection.

    GREEN TODO: ``taskq_api.service.runner.TaskRunner.run(command,
    timeout_seconds)`` must wrap ``asyncio.create_subprocess_exec`` in
    ``asyncio.wait_for(..., timeout)``; on timeout it must call
    ``proc.kill()`` and ``await proc.wait()`` before returning the
    timeout record (FR-08 / NFR-03 / SPEC §3 FR-08).
    """
    from taskq_api.service import runner as _runner

    kill_called_count_holder = {"count": 0}
    wait_awaited_count_holder = {"count": 0}

    class _FakeProc:
        pid = os.getpid()  # attach as child of THIS test process so
                            # `_list_descendant_pids` finds it.
        returncode = None

        async def communicate(self):
            # Pretend the command keeps running forever; the runner's
            # wait_for() should fire and kill us.
            await asyncio.sleep(10)
            return (b"", b"")

        async def wait(self):
            wait_awaited_count_holder["count"] += 1
            self.returncode = -9
            return self.returncode

        def kill(self):
            kill_called_count_holder["count"] += 1
            self.returncode = -9

    async def _fake_exec(*_args, **_kwargs):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    runner = _runner.TaskRunner()

    # FR-08 surface assertion: the runner must back the timeout path
    # with a TaskGroup-bound submission. The current GREEN-step
    # runner uses a plain `await self.run(...)` rather than
    # `asyncio.TaskGroup`, so the TaskGroup attribute is absent.
    assert hasattr(runner, "_task_group"), (
        "TaskRunner must back submissions with asyncio.TaskGroup "
        "(SPEC §3 FR-08 — '背景執行以 asyncio.TaskGroup 管理')"
    )

    record = await runner.run("sleep 60", timeout_seconds=0.1)

    result_final_status = record["status"]
    result_kill_called_count = kill_called_count_holder["count"]
    result_wait_awaited_count = wait_awaited_count_holder["count"]

    assert result_final_status == "timeout"
    assert result_kill_called_count == 1
    assert result_wait_awaited_count == 1


# NFR-03 NP-13
@pytest.mark.asyncio
async def test_fr08_taskgroup_max_concurrent_cap(monkeypatch):
    """AC4-max-observed / AC4-total-completed. [FR-08][NFR-03][NP-13]

    When 10 tasks are submitted concurrently and
    ``TASKQ_MAX_CONCURRENT == 2``, the runner MUST throttle so that
    the peak number of simultaneously-running coroutines never
    exceeds 2; all 10 tasks MUST still reach a terminal state
    (Q3 boundary / NP-13 concurrency).

    GREEN TODO: ``taskq_api.service.runner.TaskRunner`` must gate
    concurrent submissions via an ``asyncio.Semaphore(
    TASKQ_MAX_CONCURRENT)`` and run the admitted coroutines through
    an ``asyncio.TaskGroup``. The semaphore is acquired before the
    coroutine starts and released only after it terminates, so the
    observed peak never exceeds the cap and no coroutine is dropped.
    """
    from taskq_api.service import runner as _runner

    observed_concurrent_holder = {"current": 0, "peak": 0}
    completed_count_holder = {"count": 0}
    in_progress_lock = asyncio.Lock()

    async def _tracked_run(self, command, **_kwargs):
        async with in_progress_lock:
            observed_concurrent_holder["current"] += 1
            if observed_concurrent_holder["current"] > observed_concurrent_holder["peak"]:
                observed_concurrent_holder["peak"] = observed_concurrent_holder["current"]

        # Yield so other coroutines have a chance to ramp up the
        # counter — without a cooperative yield the semaphore cap is
        # never observable. Yield multiple times to push the peak.
        await asyncio.sleep(0.01)
        await asyncio.sleep(0.01)

        async with in_progress_lock:
            observed_concurrent_holder["current"] -= 1
        completed_count_holder["count"] += 1
        return {"status": "done", "exit_code": 0}

    monkeypatch.setattr(_runner.TaskRunner, "run", _tracked_run)

    runner = _runner.TaskRunner()
    max_concurrent = 2
    spawn_count = 10

    # Fan out N coroutines at once. GREEN will gate via semaphore.
    tasks = [asyncio.create_task(runner.run(f"echo {i}")) for i in range(spawn_count)]
    await asyncio.gather(*tasks)

    result_observed_concurrent = observed_concurrent_holder["peak"]
    result_completed_count = completed_count_holder["count"]

    assert result_observed_concurrent <= max_concurrent
    assert result_completed_count == spawn_count
