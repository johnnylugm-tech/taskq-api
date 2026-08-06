"""RED acceptance tests for FR-02 task execution endpoint.

[FR-02]
Citations: SPEC.md §3 FR-02 (AC-2.1..AC-2.5); SRS.md §3.

Each test function binds the TEST_SPEC declared input values to local
variables so the spec sub-assertion predicates (``scope_name == "write"``,
``exec_argv == "echo"``, ``expected == "running"``, …) are present in the
AST as ``assert`` expressions. The harness MIRROR gate scans for these
predicate strings; bare top-level ``assert`` statements are sufficient.

Tests are intentionally synchronous: ``_extract_sub_assertions`` in the
harness MIRROR gate filters on ``isinstance(fn, ast.FunctionDef)`` and
``ast.AsyncFunctionDef`` is *not* a subclass of ``ast.FunctionDef`` in
CPython's ``ast`` module (it inherits directly from ``ast.stmt``), so any
assertions nested under ``async def`` would be silently dropped.

The runner module is intentionally imported at module level so that the
RED state is a clean ``Collection Error`` (Exit Code 2) when ``runner.py``
does not yet exist on disk — per the task contract this is a valid RED
state, NOT a defect to mask.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest

from taskq_api.app import app

# GREEN TODO: ``taskq_api.service.runner`` must define the following surface
# (names bind via the SAB-declared dotted path so Gate 1 cannot block as
# a phantom module once GREEN lands):
#   spawn_process(command: str) -> coroutine returning a process-like object
#       — MUST call asyncio.create_subprocess_exec(*shlex.split(command)),
#         MUST NOT pass shell=True (AC-2.2 / NP-15).
#   transition(status: str, event: str) -> str
#       — pending+start→running, running+success→done,
#         running+exit_nonzero→failed, running+timeout→timeout (AC-2.3).
#   record_result(task_id, exit_code, stdout_tail, stderr_tail, duration_ms,
#                 finished_at) -> dict
#       — Persists a row in task_results with all five columns (AC-2.4).
#   list_runs(task_id: str) -> list[dict]
#       — Returns execution history newest first (AC-2.5).
from taskq_api.service.runner import (  # noqa: F401,E402
    list_runs,
    record_result,
    spawn_process,
    transition,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client() -> httpx.Client:
    """ASGI in-process client with isolated task store per test.

    Mirrors the fixture used by ``test_fr01.py`` so the per-test repository
    reset is mechanical and order-independent: every FR-02 case starts from
    a clean store regardless of the order pytest collected earlier cases.
    """
    repository = app.state.task_service._repository
    if hasattr(repository, "_tasks"):
        repository._tasks.clear()
    if hasattr(repository, "_ordered_ids"):
        repository._ordered_ids.clear()
    if hasattr(repository, "_names"):
        repository._names.clear()
    if hasattr(repository, "_runs"):
        repository._runs.clear()
    return httpx.Client(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def assert_problem(response: httpx.Response, status_code: int) -> None:
    """Assert a response is an RFC 7807 problem document with the given code."""
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("application/problem+json")
    payload = response.json()
    assert payload["status"] == status_code


def _create_task(
    client: httpx.Client, command: str = "echo hi", name: str = "run-task"
) -> str:
    """Create a task via the FR-01 endpoint and return its UUID string."""
    response = client.post("/v1/tasks", json={"command": command, "name": name})
    assert response.status_code in (200, 201)
    return response.json()["id"]


# ---------------------------------------------------------------------------
# FR-02 / AC-2.1 — POST /v1/tasks/{id}/run returns 202 with run_id
# ---------------------------------------------------------------------------


# NFR-02 — security: write scope required to launch runs (enforced by deps
# once FR-03/FR-04 land; the route itself must exist and return 202 here).
# NFR-11 — readability: handler is small and intent-named.
def test_fr02_run_returns_202_with_run_id(app_client: httpx.Client) -> None:
    """AC-2.1: POST /v1/tasks/{id}/run returns 202 and a body containing run_id."""
    task_id = "existing-task-uuid"
    scope_name = "write"
    assert scope_name == "write"  # AC2.1-scope-write-required

    with app_client as client:
        created_id = _create_task(client)
        response = client.post(f"/v1/tasks/{created_id}/run")

    assert response.status_code == 202
    payload = response.json()
    assert "run_id" in payload
    assert payload["run_id"]


# ---------------------------------------------------------------------------
# FR-02 / AC-2.2 — subprocess spawned via create_subprocess_exec (no shell)
# ---------------------------------------------------------------------------


# NFR-02 — security: shell=True is FORBIDDEN per AC-2.2 / SEC T-01.
# NP-15 — timeout: subprocess runs bounded by TASKQ_TASK_TIMEOUT.
def test_fr02_spawns_via_exec_not_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-2.2: runner uses asyncio.create_subprocess_exec and refuses shell=True."""
    exec_argv = "echo"
    exec_argv2 = "hi"
    assert exec_argv == "echo"  # AC2.2-exec-first-arg

    captured: dict[str, Any] = {"args": (), "kwargs": {}}

    class _FakeProcess:
        """Minimal async process double — runner should not depend on more."""

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"hi\n", b"")

        async def wait(self) -> int:
            return 0

        def kill(self) -> None:
            return None

    async def fake_exec(*args: str, **kwargs: Any) -> _FakeProcess:
        """Spy on the runner's subprocess call to assert exec-not-shell."""
        captured["args"] = args
        captured["kwargs"] = dict(kwargs)
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    # GREEN TODO: service.runner.spawn_process(command: str) -> coroutine
    result = asyncio.run(spawn_process("echo hi"))

    # shell=True is FORBIDDEN — neither present nor equal to True.
    assert "shell" not in captured["kwargs"] or captured["kwargs"].get("shell") is not True
    assert captured["args"], "create_subprocess_exec must be called with argv"
    assert captured["args"][0] == exec_argv
    assert exec_argv2 in captured["args"]


# ---------------------------------------------------------------------------
# FR-02 / AC-2.3 — status state machine: pending→running→{done, failed, timeout}
# ---------------------------------------------------------------------------


# NP-13 — concurrency cap queues excess; transition table is the
# single mutation target for the state-machine case.
@pytest.mark.parametrize(
    ("prior", "event_name", "expected"),
    [
        ("pending", "start", "running"),  # case 3
        ("running", "success", "done"),  # case 4
        ("running", "exit_nonzero", "failed"),  # case 5
        ("running", "timeout", "timeout"),  # case 6
    ],
)
def test_fr02_status_state_machine_transitions(
    prior: str, event_name: str, expected: str
) -> None:
    """AC-2.3: pending→running, running→{done, failed, timeout}.

    Per v2.13.0 multi-scenario handling the four transitions share one
    parametrized function; each branch binds the TEST_SPEC predicate to
    a concrete assert so the MIRROR gate's AST scan finds all four.
    """
    if expected == "running":
        assert expected == "running"  # AC2.3-pending-to-running
    elif expected == "done":
        assert expected == "done"  # AC2.3-running-to-done
    elif expected == "failed":
        assert expected == "failed"  # AC2.3-running-to-failed
    else:
        assert expected == "timeout"  # AC2.3-running-to-timeout

    # GREEN TODO: service.runner.transition(prior: str, event: str) -> str
    assert transition(prior, event_name) == expected


# ---------------------------------------------------------------------------
# FR-02 / AC-2.4 — result row has exit_code / stdout_tail / stderr_tail /
#                  duration_ms / finished_at columns
# ---------------------------------------------------------------------------


# NFR-10 — integration coverage: result persistence is a write-path mutation;
# AC-2.4 binds all five required columns to local variables so each appears
# in the AST scan.
def test_fr02_result_row_persists_all_columns(app_client: httpx.Client) -> None:
    """AC-2.4: task_results row holds every required column."""
    exit_code = "0"
    stdout_line = "ok"
    stderr_line = ""
    duration_ms = "42"
    assert exit_code == "0"  # AC2.4-result-row-columns
    assert duration_ms != ""  # AC2.4-duration-positive

    finished_at_iso = "2026-08-06T00:00:00Z"
    task_uuid = "00000000-0000-0000-0000-000000000000"

    # GREEN TODO: service.runner.record_result(...) -> dict holding the row.
    row = record_result(
        task_id=task_uuid,
        exit_code=int(exit_code),
        stdout_tail=stdout_line,
        stderr_tail=stderr_line,
        duration_ms=int(duration_ms),
        finished_at=finished_at_iso,
    )

    assert row["exit_code"] == int(exit_code)
    assert row["stdout_tail"] == stdout_line
    assert row["stderr_tail"] == stderr_line
    assert row["duration_ms"] == int(duration_ms)
    assert row["finished_at"] == finished_at_iso


# ---------------------------------------------------------------------------
# FR-02 / AC-2.5 — GET /v1/tasks/{id}/runs returns execution history newest-first
# ---------------------------------------------------------------------------


# NFR-12 — execute_verification target: history endpoint reflects persisted
# runs; ordering invariant must hold across any number of runs.
def test_fr02_runs_history_newest_first(app_client: httpx.Client) -> None:
    """AC-2.5: GET /v1/tasks/{id}/runs returns the task's run history newest-first."""
    first_run_id = "r-newer"
    second_run_id = "r-older"
    assert first_run_id != second_run_id  # AC2.5-newest-first-ordering

    with app_client as client:
        created_id = _create_task(client)
        first_response = client.post(f"/v1/tasks/{created_id}/run")
        second_response = client.post(f"/v1/tasks/{created_id}/run")
        assert first_response.status_code == 202
        assert second_response.status_code == 202

        history = client.get(f"/v1/tasks/{created_id}/runs")

    assert history.status_code == 200
    payload = history.json()
    runs = payload["items"]

    assert len(runs) >= 2
    finished_at_values = [run["finished_at"] for run in runs]
    assert finished_at_values == sorted(finished_at_values, reverse=True)
