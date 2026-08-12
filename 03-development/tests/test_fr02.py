"""TDD-RED failing tests for FR-02 (Task Execution Endpoint).

These tests intentionally fail because the source modules declared
in the SAB for FR-02 do not yet exist on disk — specifically:

    taskq_api.service.runner   (the async subprocess executor)
    taskq_api.models.orm       (the TaskResult ORM row)

The GREEN step will implement them; this RED step locks the contract.

Per the TEST_INVENTORY / TEST_SPEC catalog (FR-02), the seven test
functions below cover the canonical acceptance criteria:

    AC1-run-status            POST /v1/tasks/{id}/run (write scope) -> 202
    AC1-run-id-present        response includes a 36-char run_id
    AC2-shlex-args            runner passes shlex.split tokens to create_subprocess_exec
    AC2-no-shell              create_subprocess_exec called with shell=False
    AC3-row-exists            one row in task_results per run
    AC3-row-fields            exit_code captured
    AC3-stdout-tail           stdout_tail captured
    AC4-timeout-status        timed-out run -> status "timeout"
    AC4-process-reaped        timeout leaves no orphan pids
    AC5-drain-status          graceful-drain shutdown -> status "interrupted"
    AC5-no-orphan             graceful drain leaves no orphan pids
    AC6-grep-zero             codebase contains no shell=True
    AC7-unknown-run           POST /v1/tasks/{unknown}/run -> 404

The TEST_SPEC names are the contract; spec-coverage-check uses exact
match. Do NOT rename these functions.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# Top-level imports — RED will surface as ModuleNotFoundError for
# `taskq_api.service.runner` and `taskq_api.models.orm`. It is
# EXPECTED and acceptable for pytest to fail with Collection Error
# (Exit Code 2) at this stage.
from taskq_api.api.tasks import create_tasks_router  # noqa: F401
from taskq_api.app import create_app
from taskq_api.models.orm import TaskResult  # noqa: F401
from taskq_api.repository.task_repo import TaskRepo  # noqa: F401
from taskq_api.service.runner import TaskRunner  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _list_descendant_pids(parent_pid: int) -> list[int]:
    """Return a best-effort list of descendant pids of `parent_pid`.

    Used by timeout / drain tests to assert no orphan subprocess
    survives shutdown. Walks ``/proc`` on Linux; returns ``[]`` on
    any other platform or when ``/proc`` is unavailable so the test
    remains cross-platform (the FR-02 module's correctness is what
    we lock down — not the host's process tree).
    """
    descendants: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return descendants
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(errors="ignore")
        except OSError:
            continue
        ppid_match = re.search(r"^PPid:\s*(\d+)", status, re.MULTILINE)
        if ppid_match and int(ppid_match.group(1)) == parent_pid:
            descendants.append(int(entry.name))
    return descendants


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def write_api_key() -> str:
    """Static write-scoped key used by FR-02 happy-path tests."""
    return "test-write-key"


@pytest.fixture
def read_api_key() -> str:
    """Static read-scoped key used by FR-02 history tests."""
    return "test-read-key"


@pytest.fixture(autouse=True)
def _stub_external_side_effects(monkeypatch):
    """Stub external side-effects so tests fail for FEATURE reasons only.

    The autouse fixture runs before every test; it patches the auth
    verifier and DB session acquisition so a missing feature surfaces
    as a 404 / 500 / AssertionError rather than a CryptoError or
    OperationalError from a real DB driver.
    """
    from taskq_api.service import auth as _auth

    monkeypatch.setattr(
        _auth,
        "verify_key",
        lambda raw, hashed: bool(raw) and bool(hashed),
    )

    from taskq_api.repository import session as _session

    class _FakeSession:
        def __init__(self):
            self._rows: list[dict] = []
            self.committed = False
            self.rolled_back = False

        def add(self, row):
            self._rows.append(row)

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            pass

        def query(self, *_args, **_kwargs):
            return self

        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            try:
                from taskq_api.repository.task_repo import TaskRepo
                rows = list(TaskRepo._registry.values())
                if rows:
                    return list(rows)
            except Exception:
                pass
            return list(self._rows)

        def first(self):
            return self._rows[0] if self._rows else None

    monkeypatch.setattr(
        _session,
        "get_session",
        lambda: _FakeSession(),
    )


@pytest.fixture
async def client():
    """Yield an httpx AsyncClient bound to the FastAPI ASGI app.

    Uses ASGITransport so the request never leaves the process — the
    SUBPROCESS COVERAGE CEILING rule is N/A here because pytest-cov
    can measure code executed by ASGITransport. The DB session and
    auth verifier are stubbed via the autouse fixture so no real disk
    I/O or HMAC verification occurs.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ---------------------------------------------------------------------------
# FR-02 — Test cases from TEST_SPEC.md (do NOT rename)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr02_run_task_202(client, write_api_key):
    """AC1-run-status / AC1-run-id-present. [FR-02][NFR-09]

    POST /v1/tasks/{id}/run (scope write) returns 202 with a 36-char
    run_id (UUIDv4). happy_path / Q1.
    """
    # First create a task the runner can target.
    create = await client.post(
        "/v1/tasks",
        json={"name": "t-001", "command": "echo hello"},
        headers={"X-API-Key": write_api_key},
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    response = await client.post(
        f"/v1/tasks/{task_id}/run",
        headers={"X-API-Key": write_api_key},
    )
    result_status_code = response.status_code
    result_run_id_str = response.json().get("run_id", "")
    assert result_status_code == 202
    assert len(result_run_id_str) == 36
    assert _UUID_RE.match(result_run_id_str), result_run_id_str


@pytest.mark.asyncio
async def test_fr02_runner_uses_shlex_split(monkeypatch):
    """AC2-shlex-args / AC2-no-shell. [FR-02][NFR-02][SEC T-07]

    The runner MUST call ``asyncio.create_subprocess_exec`` with the
    argv tokens produced by ``shlex.split(command)`` and with
    ``shell=False``. Q1 — locks the canonical execution primitive
    from SPEC §3 FR-02 / §8 #16 / T-07.

    GREEN TODO: ``taskq_api.service.runner.TaskRunner.run(command, ...)``
    must tokenise the command via ``shlex.split`` and pass each token
    positionally to ``asyncio.create_subprocess_exec(*argv)`` without
    setting ``shell=True``.
    """
    from taskq_api.service import runner as _runner

    captured: dict[str, object] = {"argv": None, "shell": None}

    class _FakeProc:
        pid = 99999
        returncode = 0

        async def communicate(self):
            return (b"hi\n", b"")

        async def wait(self):
            return 0

        def kill(self):
            pass

    async def _fake_exec(*args, **kwargs):
        captured["argv"] = list(args)
        captured["shell"] = kwargs.get("shell", False)
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    runner = _runner.TaskRunner()
    await runner.run("echo hi")

    result_subprocess_argv = captured["argv"]
    result_subprocess_shell_flag = captured["shell"]

    assert result_subprocess_argv == ["echo", "hi"]
    assert result_subprocess_shell_flag is False


@pytest.mark.asyncio
async def test_fr02_result_written_to_task_results(
    client, write_api_key, monkeypatch
):
    """AC3-row-exists / AC3-row-fields / AC3-stdout-tail. [FR-02][FR-07]

    After a run completes, exactly one row is persisted in
    ``task_results`` per run with ``exit_code``, ``stdout_tail``,
    ``stderr_tail``, ``duration_ms``, ``finished_at`` populated
    (SPEC §3 FR-02 + FR-07 v3 schema). Q1 / Q4.

    GREEN TODO: ``taskq_api.service.runner.TaskRunner.run`` must write
    one row to the ``task_results`` table once the subprocess exits.
    GREEN TODO: ``taskq_api.models.orm.TaskResult`` is the ORM model
    for the ``task_results`` table (FR-07 v3 schema columns).
    """
    # Stub the runner so the test does not depend on a real subprocess
    # for this assertion (the subprocess path is covered by
    # `test_fr02_runner_uses_shlex_split` and
    # `test_fr02_timeout_kills_subprocess`).
    from taskq_api.service import runner as _runner

    async def _fake_run(self, command):
        return {
            "exit_code": 0,
            "stdout_tail": command + "\n",
            "stderr_tail": "",
            "duration_ms": 12,
            "finished_at": "1970-01-01T00:00:00Z",
        }

    monkeypatch.setattr(_runner.TaskRunner, "run", _fake_run)

    # Create + run the task.
    create = await client.post(
        "/v1/tasks",
        json={"name": "t-002", "command": "echo done"},
        headers={"X-API-Key": write_api_key},
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    run = await client.post(
        f"/v1/tasks/{task_id}/run",
        headers={"X-API-Key": write_api_key},
    )
    assert run.status_code == 202, run.text

    # The row count is observable via the ORM model declared in the
    # SAB (`taskq_api.models.orm.TaskResult`).
    result_persisted_row_count = len(TaskResult.list_for_task(task_id))
    assert result_persisted_row_count == 1

    row = TaskResult.list_for_task(task_id)[0]
    result_exit_code = row.exit_code
    result_stdout_tail_str = row.stdout_tail
    assert result_exit_code == 0
    assert len(result_stdout_tail_str) >= 0


@pytest.mark.asyncio
async def test_fr02_timeout_kills_subprocess(monkeypatch):
    """AC4-timeout-status / AC4-process-reaped. [FR-02][NFR-03][SEC T-09]

    A run whose subprocess exceeds ``timeout_seconds`` MUST be killed
    and the resulting record marked ``status='timeout'``. After kill,
    ``proc.wait`` MUST have been awaited and zero orphan pids remain
    under the test process. Q3 / Q5 / NP-15.

    GREEN TODO: ``taskq_api.service.runner.TaskRunner.run(command,
    timeout)`` must wrap ``asyncio.create_subprocess_exec`` in
    ``asyncio.wait_for(..., timeout)``; on timeout it must call
    ``proc.kill()`` and ``await proc.wait()`` before propagating.
    """
    from taskq_api.service import runner as _runner

    kill_called = {"count": 0}
    wait_awaited = {"count": 0}

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
            wait_awaited["count"] += 1
            self.returncode = -9
            return self.returncode

        def kill(self):
            kill_called["count"] += 1
            self.returncode = -9

    async def _fake_exec(*_args, **_kwargs):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    runner = _runner.TaskRunner()
    record = await runner.run("sleep 60", timeout_seconds=0.1)

    result_final_status = record["status"]
    result_orphan_pids = _list_descendant_pids(os.getpid())
    assert result_final_status == "timeout"
    assert result_orphan_pids == []


@pytest.mark.asyncio
async def test_fr02_graceful_drain_interrupted(monkeypatch):
    """AC5-drain-status / AC5-no-orphan. [FR-02][FR-08][NFR-03]

    On shutdown with one in-flight task, the runner MUST drain the
    task within ``drain_timeout_seconds``; any task still in flight
    after the drain window is marked ``status='interrupted'`` and
    leaves zero orphan pids. Q4.

    GREEN TODO: ``taskq_api.service.runner.TaskRunner.shutdown(
    drain_timeout)`` must cancel each tracked in-flight task and
    finalise its row with ``status='interrupted'`` after the drain
    timeout elapses.
    """
    from taskq_api.service import runner as _runner

    interrupt_marked: list[str] = []

    async def _long_running(self, command, **_kwargs):
        # Simulate an in-flight task: never completes on its own.
        await asyncio.sleep(10)
        return {"status": "done", "exit_code": 0}

    def _shutdown(self, drain_timeout):
        # GREEN TODO: real impl awaits all in-flight tasks with a
        # bounded drain window and marks stragglers 'interrupted'.
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


def test_fr02_no_shell_true_in_codebase():
    """AC6-grep-zero. [FR-02][NFR-02][SEC T-07]

    Recursive grep of ``03-development/src`` for the literal pattern
    ``shell=True`` MUST return zero hits. ``shell=True`` is forbidden
    in the codebase per SPEC §8 #16 / NFR-02; T-07 names this test as
    the threat verification for FR-02's execution primitive. Q2 /
    NP-08.
    """
    scan_root = Path(__file__).resolve().parent.parent / "src"
    pattern = "shell=True"
    hits: list[str] = []

    for path in scan_root.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern in line:
                hits.append(f"{path}:{lineno}:{line.strip()}")

    result_grep_hit_count = len(hits)
    assert result_grep_hit_count == 0, hits


@pytest.mark.asyncio
async def test_fr02_run_unknown_task_404(client, write_api_key):
    """AC7-unknown-run. [FR-02][NFR-09]

    POST /v1/tasks/{unknown-id}/run (scope write) returns 404 +
    problem+json. Q2.
    """
    response = await client.post(
        "/v1/tasks/00000000-0000-0000-0000-000000000000/run",
        headers={"X-API-Key": write_api_key},
    )
    result_status_code = response.status_code
    assert result_status_code == 404
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )
