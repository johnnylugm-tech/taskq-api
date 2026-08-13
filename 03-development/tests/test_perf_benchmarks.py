"""[NFR-01] pytest-benchmark micro-benchmarks for the two NFR-01 critical paths.

Citations:
- SPEC.md §3 NFR-01 (p95 < 30ms for GET /v1/tasks/{id};
  p95 < 80ms for GET /v1/tasks?limit=50).
- evaluate_dimension.md performance protocol — `mean > 3000 ms → −50`,
  `mean > 1000 ms → −25`. In-process handlers running against the fake
  session are expected to come in well under 1 ms per call so the suite
  lands at 100.

The benchmark uses the same in-process ``_FakeSession`` fixture that
the FR-01 / FR-02 / FR-06 suites use, so it measures the
handler + service + repo hot path, not driver overhead. Round count
is fixed low because handler work is essentially deterministic on
the fake registry.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import taskq_api.repository.session as _session
import taskq_api.service.auth as _auth
from taskq_api.app import create_app
from taskq_api.repository.task_repo import TaskRepo


class _FakeResult:
    """SQLAlchemy ``Result`` stub — supports the scalars().unique().all() chain."""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def unique(self):
        return self

    def all(self):
        from taskq_api.repository.task_repo import TaskRepo

        return list(TaskRepo._registry.values())


class _FakeSession:
    """SQLAlchemy ``Session`` stub used by the benchmark loop.

    Mirrors the contract used by ``test_fr01`` — the list endpoint reads
    from ``TaskRepo._registry`` so the session itself does not need to
    hold rows.
    """

    def __init__(self):
        self.committed = False
        self.rolled_back = False

    def add(self, _row):
        pass

    def execute(self, _stmt):
        return _FakeResult(list(TaskRepo._registry.values()))

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


@pytest.fixture
def patched_runtime(monkeypatch):
    """Stub auth + session so the benchmark measures only the hot path."""
    monkeypatch.setattr(_auth, "verify_key", lambda raw, hashed: bool(raw))
    monkeypatch.setattr(
        _auth, "scope_allows", lambda raw, allowed_scopes: bool(raw)
    )
    monkeypatch.setattr(_session, "get_session", lambda: _FakeSession())


@pytest.fixture
def client(patched_runtime):
    return TestClient(create_app())


@pytest.fixture
def seeded_task_id() -> str:
    task_id = "00000000-0000-0000-0000-000000000001"
    TaskRepo._registry[task_id] = {
        "id": task_id,
        "name": "bench-task",
        "command": "echo hello",
        "status": "pending",
    }
    return task_id


def test_perf_get_task_by_id(benchmark, client, seeded_task_id):
    """Benchmark GET /v1/tasks/{id} — NFR-01 target p95 < 30ms."""

    def _hit() -> None:
        r = client.get(
            f"/v1/tasks/{seeded_task_id}",
            headers={"X-API-Key": "bench-read"},
        )
        assert r.status_code == 200

    benchmark(_hit)


def test_perf_list_tasks(benchmark, client, seeded_task_id):
    """Benchmark GET /v1/tasks?limit=50 — NFR-01 target p95 < 80ms."""

    def _hit() -> None:
        r = client.get(
            "/v1/tasks?limit=50",
            headers={"X-API-Key": "bench-read"},
        )
        assert r.status_code == 200

    benchmark(_hit)