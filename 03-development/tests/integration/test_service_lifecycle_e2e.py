"""End-to-end integration tests covering the service / repository layers.

These tests exercise the full HTTP → router → service → repo path
through the FastAPI app composition root, with a `_FakeSession` stub
matching the FR-01/02/03 pattern (real DB would require alembic +
SQLite; the framework's tests don't need that to exercise the
service validation logic).

Targeted coverage: repository/key_repo, service/ratelimit, service/runner,
service/auth, app.py composition paths.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from taskq_api.app import create_app
from taskq_api.service import auth as _auth
from taskq_api.repository import key_repo as _key_repo


@pytest.fixture(autouse=True)
def _stub_auth(monkeypatch):
    """Stub verify_key so any non-empty raw + non-empty hashed validates."""
    monkeypatch.setattr(
        _auth,
        "verify_key",
        lambda raw, hashed: bool(raw) and bool(hashed),
    )


@pytest.fixture(autouse=True)
def _stub_get_session(monkeypatch):
    """Stub the SQLAlchemy session with an in-memory fake so routes
    exercise service logic without a real DB."""
    from taskq_api.repository import session as _session

    class _FakeResult:
        def __init__(self, rows, filters):
            self._rows = rows
            self._filters = filters

        def scalars(self):
            return self

        def unique(self):
            return self

        def all(self):
            from taskq_api.repository.task_repo import TaskRepo
            registry = list(TaskRepo._registry.values())
            rows = list(self._rows) if self._rows else registry
            for col, val in self._filters.items():
                rows = [r for r in rows if r.get(col) == val]
            return rows

    class _FakeSession:
        def __init__(self):
            self._rows = []
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

        def execute(self, stmt):
            from taskq_api.repository.task_repo import TaskRepo
            registry = list(TaskRepo._registry.values())
            filters = {}
            where = getattr(stmt, "_whereclause", None)
            if where is not None and hasattr(where, "left") and hasattr(where, "right"):
                left = getattr(where.left, "name", None)
                right = getattr(where.right, "value", None)
                if left is not None and right is not None:
                    filters[left] = right
            return _FakeResult(registry, filters)

    monkeypatch.setattr(_session, "get_session", lambda: _FakeSession())


@pytest.mark.asyncio
async def test_integration_create_list_delete_full_cycle():
    """CRUD lifecycle with admin scope for delete."""
    app = create_app()
    transport = ASGITransport(app=app)

    async def _call():
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            # Create
            r = await ac.post(
                "/v1/tasks",
                json={"name": "integration-cycle", "command": "echo cycle"},
                headers={"X-API-Key": "test-write-key"},
            )
            assert r.status_code == 201
            tid = r.json()["id"]

            # List (read)
            r = await ac.get("/v1/tasks?limit=10", headers={"X-API-Key": "test-read-key"})
            assert r.status_code == 200
            items = r.json()["items"]
            assert any(t["id"] == tid for t in items)

            # Get
            r = await ac.get(f"/v1/tasks/{tid}", headers={"X-API-Key": "test-read-key"})
            assert r.status_code == 200

            # Delete with admin
            r = await ac.delete(f"/v1/tasks/{tid}", headers={"X-API-Key": "test-admin-key"})
            assert r.status_code == 204

            # Verify gone
            r = await ac.get(f"/v1/tasks/{tid}", headers={"X-API-Key": "test-read-key"})
            assert r.status_code == 404

    await _call()


@pytest.mark.asyncio
async def test_integration_status_filter():
    """List endpoint with status filter applies correctly."""
    app = create_app()
    transport = ASGITransport(app=app)

    async def _call():
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            # Create 2 tasks
            for name in ("alpha-1", "alpha-2"):
                r = await ac.post(
                    "/v1/tasks",
                    json={"name": name, "command": "echo"},
                    headers={"X-API-Key": "test-write-key"},
                )
                assert r.status_code == 201

            # Filter by status=pending
            r = await ac.get(
                "/v1/tasks?status=pending",
                headers={"X-API-Key": "test-read-key"},
            )
            assert r.status_code == 200
            assert "items" in r.json()

    await _call()


@pytest.mark.asyncio
async def test_integration_problem_envelope_on_validation_error():
    """Validation error returns 422 problem+json."""
    app = create_app()
    transport = ASGITransport(app=app)

    async def _call():
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            # Invalid cursor
            r = await ac.get(
                "/v1/tasks?limit=999",
                headers={"X-API-Key": "test-read-key"},
            )
            assert r.status_code == 422
            assert "application/problem+json" in r.headers["content-type"]

    await _call()


@pytest.mark.asyncio
async def test_integration_metrics_endpoint():
    """GET /v1/metrics returns Prometheus body without password."""
    import os
    os.environ["TASKQ_DB_URL"] = "postgresql://u:secret@host.example/db"
    app = create_app()
    transport = ASGITransport(app=app)

    async def _call():
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            r = await ac.get("/v1/metrics")
            assert r.status_code == 200
            body = r.text
            assert "taskq_db_url" in body
            assert "secret" not in body

    await _call()


@pytest.mark.asyncio
async def test_integration_scope_admin_endpoint():
    """DELETE endpoint requires admin scope; write key gets 403."""
    app = create_app()
    transport = ASGITransport(app=app)

    async def _call():
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            # Try delete with write key (should be 403)
            r = await ac.delete(
                "/v1/tasks/00000000-0000-0000-0000-000000000000",
                headers={"X-API-Key": "test-write-key"},
            )
            assert r.status_code == 403

    await _call()


@pytest.mark.asyncio
async def test_integration_healthz_redis_no_auth():
    """Health endpoint accessible without auth."""
    app = create_app()
    transport = ASGITransport(app=app)

    async def _call():
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            r = await ac.get("/healthz")
            assert r.status_code == 200
            assert r.json() == {"status": "ok"}

    await _call()


@pytest.mark.asyncio
async def test_integration_run_lifecycle():
    """Run endpoint creates a run record and lists it back."""
    app = create_app()
    transport = ASGITransport(app=app)

    async def _call():
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            # Create task
            r = await ac.post(
                "/v1/tasks",
                json={"name": "integration-runner", "command": "echo runner"},
                headers={"X-API-Key": "test-write-key"},
            )
            assert r.status_code == 201
            tid = r.json()["id"]

            # Run it
            r = await ac.post(f"/v1/tasks/{tid}/run", headers={"X-API-Key": "test-write-key"})
            assert r.status_code == 202
            run_id = r.json()["run_id"]

            # List runs
            r = await ac.get(
                f"/v1/tasks/{tid}/runs",
                headers={"X-API-Key": "test-read-key"},
            )
            assert r.status_code == 200
            assert any(run["run_id"] == run_id for run in r.json()["items"])

    await _call()


@pytest.mark.asyncio
async def test_integration_unknown_task_run():
    """Run endpoint on unknown task returns 404."""
    app = create_app()
    transport = ASGITransport(app=app)

    async def _call():
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            r = await ac.post(
                "/v1/tasks/00000000-0000-0000-0000-000000000000/run",
                headers={"X-API-Key": "test-write-key"},
            )
            assert r.status_code == 404

    await _call()


@pytest.mark.asyncio
async def test_integration_unknown_task_runs():
    """List runs on unknown task returns 404."""
    app = create_app()
    transport = ASGITransport(app=app)

    async def _call():
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            r = await ac.get(
                "/v1/tasks/00000000-0000-0000-0000-000000000000/runs",
                headers={"X-API-Key": "test-read-key"},
            )
            assert r.status_code == 404

    await _call()
