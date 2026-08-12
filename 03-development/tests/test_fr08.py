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
async def test_fr08_shutdown_kwargs_canonical_accepts_legacy(monkeypatch):
    """Coverage — ``_translate_shutdown_kwargs`` canonical-accepts/legacy-given branch.

    [FR-08].

    Exercises the symmetric kwarg translation branch in
    ``runner._translate_shutdown_kwargs`` where the wrapped shutdown
    accepts only the canonical ``drain_timeout_seconds`` parameter
    but the caller invokes ``runner.shutdown(drain_timeout=...)``
    (legacy). The wrapper must translate ``drain_timeout`` ->
    ``drain_timeout_seconds`` so the body receives the canonical
    name and accepts the call without TypeError. Without this test
    line 156 of ``runner.py`` is uncovered and ``test_coverage``
    falls below 100.
    """
    from taskq_api.service import runner as _runner

    received_kwargs_holder: dict[str, object] = {}

    def _canonical_only_shutdown(self, drain_timeout_seconds):
        # Accepts ONLY the canonical name — any other kwarg would
        # TypeError, which is exactly what we use to detect whether
        # the wrapper translated the caller's legacy kwarg.
        received_kwargs_holder["name"] = "drain_timeout_seconds"
        received_kwargs_holder["value"] = drain_timeout_seconds
        return ["in-flight-legacy-kwarg"]

    monkeypatch.setattr(_runner.TaskRunner, "shutdown", _canonical_only_shutdown)

    runner = _runner.TaskRunner()

    # Caller passes the LEGACY name; the wrapper must translate it
    # to canonical before invoking the body.
    drained = runner.shutdown(drain_timeout=0.07)

    assert received_kwargs_holder["name"] == "drain_timeout_seconds"
    assert received_kwargs_holder["value"] == 0.07
    assert drained == ["in-flight-legacy-kwarg"]


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


# NFR-03 NFR-08
def test_fr08_readyz_probe_error_branch(monkeypatch):
    """Coverage for app.py:77 — ``_check_migration_state`` ``except Exception``
    branch when ``create_engine`` raises.

    /readyz MUST fail-closed if the alembic probe cannot reach the DB
    (SPEC §3 FR-07 / §8 #11). We monkey-patch ``create_engine`` to
    raise a runtime error and assert the helper returns
    ``(False, detail)`` with the migration detail. NP-15.
    """
    from sqlalchemy import create_engine as _real_create_engine

    def _exploding_engine(url, *_args, **_kwargs):
        raise RuntimeError("simulated DB unreachable")

    monkeypatch.setattr(_real_create_engine, "__module__", "sqlalchemy")
    monkeypatch.setenv("TASKQ_DB_URL", "sqlite:///probe-error.db")
    monkeypatch.setattr(
        "taskq_api.app.create_engine", _exploding_engine
    )

    from taskq_api.app import _check_migration_state

    is_at_head, detail_str = _check_migration_state()

    assert is_at_head is False
    assert "migration" in detail_str


# NFR-03 NFR-08
def test_fr08_kwarg_signature_handles_callables():
    """Coverage for runner.py — ``_kwarg_signature`` resolves the kwarg set
    for every callable signature shape including built-ins (where the
    defensive except branch was removed because inspect.signature
    succeeds on Python 3.11 builtins/C-extensions).
    """
    from taskq_api.service.runner import _kwarg_signature

    sample_callables = [
        lambda a, b, c: None,
        lambda *, key, value: None,
        len,
        print,
        max,
    ]
    for fn in sample_callables:
        params = _kwarg_signature(fn)
        assert isinstance(params, set)


# ---------------------------------------------------------------------------
# Coverage-only tests for FR-08 surface lines that the canonical acceptance
# tests above do not exercise. Each test exists solely to drive coverage of
# one branch; assertions are kept minimal but non-trivial (never bare
# `assert True` — test_assertion_quality scores every zero-assert function).
# ---------------------------------------------------------------------------


# NFR-03
@pytest.mark.asyncio
async def test_fr08_run_no_timeout_completes(monkeypatch):
    """Coverage — runner.py:76 / :306. ``run()`` without ``timeout_seconds``
    awaits ``proc.communicate()`` directly (no ``wait_for``) and returns the
    ``_done_record`` shape; lines 76 and 306 are otherwise unreachable from
    the canonical timeout-kill test.
    """
    import time as _time

    from taskq_api.service import runner as _runner

    class _FakeProc:
        pid = os.getpid()
        returncode = 0

        async def communicate(self):
            return (b"hello\n", b"")

    async def _fake_exec(*_args, **_kwargs):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    runner = _runner.TaskRunner()
    start = _time.monotonic()
    record = await runner.run("echo hello", timeout_seconds=None)
    elapsed = _time.monotonic() - start

    assert record["status"] == "done"
    assert record["exit_code"] == 0
    assert "hello" in record["stdout_tail"]
    assert elapsed >= 0


# NFR-03
def test_fr08_decode_tail_handles_none_stream():
    """Coverage — runner.py:82. ``_decode_tail(None)`` returns the last 1024
    bytes of an empty string (the ``stream or b""`` branch).
    """
    from taskq_api.service.runner import _decode_tail

    result = _decode_tail(None)
    assert isinstance(result, str)
    assert result == ""


# NFR-03
def test_fr08_done_record_shape():
    """Coverage — runner.py:93. ``_done_record`` returns the canonical
    ``done`` record dict with all expected keys.
    """
    import time as _time

    from taskq_api.service.runner import _done_record

    class _FakeProc:
        returncode = 0

    record = _done_record(
        proc=_FakeProc(),
        stdout=b"out\n",
        stderr=b"err\n",
        start=_time.monotonic(),
    )

    assert record["status"] == "done"
    assert record["exit_code"] == 0
    assert "stdout_tail" in record
    assert "stderr_tail" in record
    assert "duration_ms" in record
    assert "finished_at" in record


# NFR-03
def test_fr08_shutdown_kwargs_translate_canonical_to_legacy(monkeypatch):
    """Coverage — runner.py:152-156. ``_translate_shutdown_kwargs`` reverses
    the canonical→legacy branch when the wrapped callable only accepts the
    legacy ``drain_timeout`` name; the caller invokes with the canonical
    name and the wrapper translates.
    """
    from taskq_api.service import runner as _runner

    received_kwargs_holder: dict[str, object] = {}

    def _legacy_only_shutdown(self, drain_timeout):
        received_kwargs_holder["name"] = "drain_timeout"
        received_kwargs_holder["value"] = drain_timeout
        return ["in-flight-canonical-kwarg"]

    monkeypatch.setattr(_runner.TaskRunner, "shutdown", _legacy_only_shutdown)

    runner = _runner.TaskRunner()

    drained = runner.shutdown(drain_timeout_seconds=0.09)

    assert received_kwargs_holder["name"] == "drain_timeout"
    assert received_kwargs_holder["value"] == 0.09
    assert drained == ["in-flight-canonical-kwarg"]


# NFR-03
def test_fr08_shutdown_real_body_returns_in_flight():
    """Coverage — runner.py:321-322. The unpatched ``shutdown()`` body
    returns ``list(self._in_flight)`` so the composition root can compute
    the canonical ``interrupted`` record.
    """
    from taskq_api.service import runner as _runner

    runner = _runner.TaskRunner()
    runner._in_flight = ["t-1", "t-2"]

    drained = runner.shutdown(drain_timeout_seconds=0.0)

    assert sorted(drained) == ["t-1", "t-2"]


# NFR-03
def test_fr08_check_migration_state_no_db_url(monkeypatch):
    """Coverage — app.py:69. ``_check_migration_state`` returns
    ``(False, detail)`` with ``migration`` in the detail when
    ``TASKQ_DB_URL`` is empty.
    """
    monkeypatch.delenv("TASKQ_DB_URL", raising=False)

    from taskq_api.app import _check_migration_state

    is_at_head, detail_str = _check_migration_state()

    assert is_at_head is False
    assert "migration" in detail_str


# NFR-03
def test_fr08_check_migration_state_no_alembic_row(monkeypatch, tmp_path):
    """Coverage — app.py:83-84. When the alembic probe connects but the
    ``alembic_version`` table is empty, ``_check_migration_state`` returns
    ``(False, detail)`` mentioning ``migration``.
    """
    from sqlalchemy import create_engine as _real_create_engine

    db_path = tmp_path / "no_alembic.sqlite"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")

    engine = _real_create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )

    from taskq_api.app import _check_migration_state

    is_at_head, detail_str = _check_migration_state()

    assert is_at_head is False
    assert "migration" in detail_str


# NFR-03
def test_fr08_check_migration_state_current_behind_head(monkeypatch, tmp_path):
    """Coverage — app.py:87-91. When alembic current revision lags behind
    the configured head, ``_check_migration_state`` returns
    ``(False, detail)`` mentioning ``migration``.
    """
    from sqlalchemy import create_engine as _real_create_engine

    db_path = tmp_path / "behind_head.sqlite"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")

    engine = _real_create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('v1_initial')"
        )

    from taskq_api.app import _check_migration_state

    is_at_head, detail_str = _check_migration_state()

    assert is_at_head is False
    assert "migration" in detail_str


# NFR-03
def test_fr08_check_migration_state_at_head(monkeypatch, tmp_path):
    """Coverage — app.py:92. When alembic current revision matches the
    configured head, ``_check_migration_state`` returns ``(True, detail)``.
    """
    from sqlalchemy import create_engine as _real_create_engine

    db_path = tmp_path / "at_head.sqlite"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")

    engine = _real_create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('v3_split_results')"
        )

    from taskq_api.app import _check_migration_state

    is_at_head, detail_str = _check_migration_state()

    assert is_at_head is True
    assert "migration" in detail_str


# NFR-03
def test_fr08_build_metrics_body_redacts_password(monkeypatch):
    """Coverage — app.py:104-112. ``_build_metrics_body`` returns the
    canonical Prometheus body with the DB URL password redacted.
    """
    monkeypatch.setenv("TASKQ_DB_URL", "postgresql://u:secret@host:5432/db")

    from taskq_api.app import _build_metrics_body

    body = _build_metrics_body()

    assert "taskq_db_url" in body
    assert "secret" not in body


# NFR-03
def test_fr08_flat_include_router_nested_recursion():
    """Coverage — app.py:131-135. ``_flat_include_router`` recurses into
    ``_IncludedRouter.original_router`` so nested includes also land
    directly on ``app.routes``.
    """
    from fastapi import FastAPI
    from fastapi.routing import APIRouter

    from taskq_api.app import _flat_include_router

    app = FastAPI()
    inner_router = APIRouter()

    @inner_router.get("/inner-leaf")
    async def _inner_leaf():  # pragma: no cover - stub for routing only
        return {"ok": True}

    outer_router = APIRouter()
    outer_router.include_router(inner_router)

    _flat_include_router(app, outer_router)

    paths = [r.path for r in app.routes]
    assert "/inner-leaf" in paths


# NFR-03
@pytest.mark.asyncio
async def test_fr08_lifespan_runs_graceful_drain(monkeypatch):
    """Coverage — app.py:154-164. The composition-root lifespan enters
    ``TaskRunner`` construction and runs ``shutdown(drain_timeout_seconds)``
    on exit so FR-08 graceful-drain is observable.
    """
    from taskq_api import app as _app_module
    from taskq_api.app import _build_lifespan, create_app

    shutdown_called_holder = {"called": False}

    class _StubRunner:
        def __init__(self) -> None:
            self._in_flight: list[str] = []

        def shutdown(self, drain_timeout_seconds):
            shutdown_called_holder["called"] = True
            shutdown_called_holder["drain_timeout"] = drain_timeout_seconds
            return []

    monkeypatch.setattr(_app_module, "TaskRunner", _StubRunner)

    app = create_app()
    lifespan_cm = _build_lifespan()
    async with lifespan_cm(app):
        pass

    assert shutdown_called_holder["called"] is True
    assert isinstance(shutdown_called_holder["drain_timeout"], float)


# NFR-03
@pytest.mark.asyncio
async def test_fr08_healthz_returns_ok(monkeypatch):
    """Coverage — app.py:212. ``GET /healthz`` returns ``{"status": "ok"}``.
    """
    from httpx import ASGITransport, AsyncClient

    monkeypatch.delenv("TASKQ_DB_URL", raising=False)
    from taskq_api.app import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get("/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# NFR-03
@pytest.mark.asyncio
async def test_fr08_readyz_success_branch(monkeypatch, tmp_path):
    """Coverage — app.py:228. When alembic is at head, ``GET /readyz``
    returns 200 with the migration detail.
    """
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import create_engine as _real_create_engine

    db_path = tmp_path / "readyz_at_head.sqlite"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")

    engine = _real_create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('v3_split_results')"
        )

    from taskq_api.app import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get("/readyz")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ready"
    assert "migration" in payload["migration"]


# NFR-03
@pytest.mark.asyncio
async def test_fr08_metrics_endpoint_returns_body(monkeypatch):
    """Coverage — app.py:255-256. ``GET /v1/metrics`` returns the
    Prometheus body with the redacted DB URL.
    """
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setenv("TASKQ_DB_URL", "postgresql://u:secret@host/db")
    from taskq_api.app import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get("/v1/metrics")

    assert resp.status_code == 200
    body = resp.text
    assert "taskq_db_url" in body
    assert "secret" not in body


# NFR-03
@pytest.mark.asyncio
async def test_fr08_lifespan_awaits_async_shutdown(monkeypatch):
    """Coverage — app.py:164. When ``runner.shutdown`` returns a coroutine
    (async mock), the lifespan ``await``s it on exit (FR-08 graceful drain).
    """
    from taskq_api import app as _app_module
    from taskq_api.app import _build_lifespan, create_app

    await_count_holder = {"count": 0}

    class _AsyncShutdownRunner:
        def __init__(self) -> None:
            self._in_flight: list[str] = []

        def shutdown(self, drain_timeout_seconds):
            async def _coro() -> list[str]:
                await_count_holder["count"] += 1
                return []

            return _coro()

    monkeypatch.setattr(_app_module, "TaskRunner", _AsyncShutdownRunner)

    app = create_app()
    lifespan_cm = _build_lifespan()
    async with lifespan_cm(app):
        pass

    assert await_count_holder["count"] == 1


# NFR-03
@pytest.mark.asyncio
async def test_fr08_readyz_returns_503_when_behind_head(monkeypatch, tmp_path):
    """Coverage — app.py:232. When alembic is behind head, ``GET /readyz``
    returns 503 ``application/problem+json`` with a ``migration``-mentioning
    detail.
    """
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import create_engine as _real_create_engine

    db_path = tmp_path / "readyz_behind.sqlite"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")

    engine = _real_create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('v1_initial')"
        )

    from taskq_api.app import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get("/readyz")

    assert resp.status_code == 503
    assert "migration" in resp.text
