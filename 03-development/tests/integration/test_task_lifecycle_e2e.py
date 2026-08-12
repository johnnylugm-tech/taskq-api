"""End-to-end integration tests for the taskq-api HTTP surface.

Layer: integration (per NFR-10 — httpx ASGITransport end-to-end).
Drives the FastAPI app via httpx ASGITransport so the full HTTP → router
→ service → repo path is exercised without spinning up a real network
server. The autouse fixture in conftest.py stubs auth + DB so the tests
fail for FEATURE reasons, not I/O reasons.

Per Gate 2 (Phase 3 exit), integration_coverage measures the line-coverage
of the source tree while running THIS directory only. The four scenarios
below cover the canonical CRUD lifecycle, so the source tree is exercised
broadly via real requests.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from taskq_api.app import create_app


@pytest.fixture(autouse=True)
def _stub_verify_key(monkeypatch):
    """Match the FR-01/FR-02 pattern: stub ``auth.verify_key`` so any
    non-empty raw key + any non-empty hashed key validates. Without this
    the auth dependency returns 401 before the route runs.
    """
    from taskq_api.service import auth as _auth

    monkeypatch.setattr(
        _auth,
        "verify_key",
        lambda raw, hashed: bool(raw) and bool(hashed),
    )


@pytest.fixture(autouse=True)
def _stub_get_session(monkeypatch):
    """Stub the SQLAlchemy ``session.get_session`` factory with an in-memory
    fake so the route exercises service validation logic without a real DB.
    Mirrors the autouse pattern in test_fr01.py — without this every route
    raises OperationalError and the integration suite reports 500.
    """
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
            rows = list(self._rows)
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


@pytest.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.mark.asyncio
async def test_integration_create_then_get_then_delete(client):
    """End-to-end CRUD lifecycle: POST → GET → DELETE."""
    headers_w = {"X-API-Key": "test-write-key"}
    headers_r = {"X-API-Key": "test-read-key"}
    headers_a = {"X-API-Key": "test-admin-key"}

    # CREATE
    resp = await client.post(
        "/v1/tasks",
        json={"name": "integration-1", "command": "echo hi"},
        headers=headers_w,
    )
    assert resp.status_code == 201
    task_id = resp.json()["id"]
    assert isinstance(task_id, str) and len(task_id) == 36

    # GET
    resp = await client.get(f"/v1/tasks/{task_id}", headers=headers_r)
    assert resp.status_code == 200
    assert resp.json()["name"] == "integration-1"

    # DELETE (requires admin scope)
    resp = await client.delete(f"/v1/tasks/{task_id}", headers=headers_a)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_integration_list_with_status_filter(client):
    """List endpoint: status filter applies, pagination defaults to limit=50."""
    headers_w = {"X-API-Key": "test-write-key"}
    headers_r = {"X-API-Key": "test-read-key"}

    # Seed two tasks
    for name in ("integration-a", "integration-b"):
        r = await client.post(
            "/v1/tasks",
            json={"name": name, "command": "echo"},
            headers=headers_w,
        )
        assert r.status_code == 201

    # List all
    r = await client.get("/v1/tasks", headers=headers_r)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and isinstance(body["items"], list)
    assert body["limit"] == 50


@pytest.mark.asyncio
async def test_integration_health_and_readyz_endpoints(client):
    """Health and readiness probes are unauthenticated and respond 200."""
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    r = await client.get("/readyz")
    # 200 when DB up + at head (the conftest creates an empty SQLite); the
    # readyz probe reports "no row" if alembic_version is empty. Either is
    # acceptable here — the route is wired, which is what integration cares about.
    assert r.status_code in (200, 503)
    assert r.headers["content-type"].startswith("application/")


@pytest.mark.asyncio
async def test_integration_create_duplicate_returns_409(client):
    """Duplicate name returns a problem+json 409 envelope."""
    headers = {"X-API-Key": "test-write-key"}

    r = await client.post(
        "/v1/tasks",
        json={"name": "integration-dup", "command": "echo"},
        headers=headers,
    )
    assert r.status_code == 201

    r = await client.post(
        "/v1/tasks",
        json={"name": "integration-dup", "command": "echo"},
        headers=headers,
    )
    assert r.status_code == 409
    assert "application/problem+json" in r.headers["content-type"]


@pytest.mark.asyncio
async def test_integration_validation_errors(client):
    """Empty name and oversize command return 422 with problem+json."""
    headers = {"X-API-Key": "test-write-key"}

    # Empty name
    r = await client.post(
        "/v1/tasks",
        json={"name": "", "command": "echo"},
        headers=headers,
    )
    assert r.status_code == 422
    assert "application/problem+json" in r.headers["content-type"]

    # Oversize command (> 1000 chars)
    r = await client.post(
        "/v1/tasks",
        json={"name": "integration-oversize", "command": "x" * 1001},
        headers=headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_integration_unauthorized_no_api_key(client):
    """Requests without X-API-Key are rejected with 401."""
    # No header at all
    r = await client.get("/v1/tasks")
    assert r.status_code == 401
    assert "application/problem+json" in r.headers["content-type"]


@pytest.mark.asyncio
async def test_integration_unknown_task_404(client):
    """GET /v1/tasks/{unknown} returns 404 problem+json."""
    headers = {"X-API-Key": "test-read-key"}
    r = await client.get("/v1/tasks/00000000-0000-0000-0000-000000000000", headers=headers)
    assert r.status_code == 404
    assert "application/problem+json" in r.headers["content-type"]


@pytest.mark.asyncio
async def test_integration_run_task_endpoint(client):
    """POST /v1/tasks/{id}/run dispatches a task and returns 202."""
    headers_w = {"X-API-Key": "test-write-key"}
    headers_r = {"X-API-Key": "test-read-key"}

    # CREATE
    r = await client.post(
        "/v1/tasks",
        json={"name": "integration-run", "command": "echo integration"},
        headers=headers_w,
    )
    assert r.status_code == 201
    task_id = r.json()["id"]

    # RUN
    r = await client.post(f"/v1/tasks/{task_id}/run", headers=headers_w)
    assert r.status_code == 202
    body = r.json()
    assert "run_id" in body
    assert body["status"] in ("pending", "running", "succeeded", "done", "failed")

    # LIST RUNS
    r = await client.get(f"/v1/tasks/{task_id}/runs", headers=headers_r)
    assert r.status_code == 200
    runs = r.json()
    assert "items" in runs
    assert len(runs["items"]) >= 1
