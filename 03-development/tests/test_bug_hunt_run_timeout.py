"""Bug-hunt repro: api/tasks.py:177 calls TaskRunner().run() without
timeout_seconds. A long-running subprocess blocks the worker indefinitely
because the runner's wait_for() path is only reached when timeout_seconds
is supplied.

RED proof: monkeypatch TaskRunner.run to assert timeout_seconds is forwarded
by the HTTP handler. With the bug present, the call receives None and the
subprocess can hang forever.
"""
from __future__ import annotations


import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    from taskq_api.service import auth as _auth
    from taskq_api.repository import session as _session

    monkeypatch.setattr(_auth, "verify_key", lambda raw, hashed: bool(raw))

    class _FakeSession:
        def add(self, row): pass
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass
        def query(self, *a, **k): return self
        def filter(self, *a, **k): return self
        def all(self): return []
        def first(self): return None
        def execute(self, stmt, *a, **k):
            class _R:
                def scalars(self_): return self_
                def unique(self_): return self_
                def all(self_): return []
            return _R()

    monkeypatch.setattr(_session, "get_session", lambda: _FakeSession())

    from taskq_api.repository.key_repo import KeyRepo
    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()
    for scope, key in (("write", "test-write-key"), ("read", "test-read-key")):
        kid = f"key-{scope}-{key}"
        KeyRepo._registry[kid] = {"id": kid, "scope": scope, "key_hash": "0" * 64, "revoked_at": None}
        KeyRepo._by_key[key] = kid


@pytest.mark.asyncio
async def test_run_endpoint_forwards_timeout_seconds(monkeypatch):
    """The run-task handler MUST forward a timeout to TaskRunner.run.

    Bug: api/tasks.py:177 calls `TaskRunner().run(task["command"])` without
    the kwarg; the runner's asyncio.wait_for branch is unreachable.
    """
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "5")

    from taskq_api.service import runner as _runner

    captured: dict = {"timeout_seconds": "UNSET"}

    async def _fake_run(self, command, *, timeout_seconds=None, **_kw):
        captured["timeout_seconds"] = timeout_seconds
        return {
            "exit_code": 0,
            "stdout_tail": "ok",
            "stderr_tail": "",
            "duration_ms": 1,
            "finished_at": "2026-08-13T00:00:00Z",
            "status": "done",
        }

    monkeypatch.setattr(_runner.TaskRunner, "run", _fake_run)

    from taskq_api.app import create_app
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        create = await ac.post(
            "/v1/tasks",
            json={"name": "run-timeout-1", "command": "sleep 999"},
            headers={"X-API-Key": "test-write-key"},
        )
        assert create.status_code == 201
        task_id = create.json()["id"]

        await ac.post(
            f"/v1/tasks/{task_id}/run",
            headers={"X-API-Key": "test-write-key"},
        )

    # Bug: timeout_seconds is None when bug present.
    assert captured["timeout_seconds"] is not None, (
        "api/tasks.py run_task did not forward TASKQ_TASK_TIMEOUT to TaskRunner.run"
    )
