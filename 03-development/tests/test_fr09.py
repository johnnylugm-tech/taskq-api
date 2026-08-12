"""TDD-RED failing tests for FR-09 (健康檢查與可觀測性 — Health & Readiness).

These tests intentionally fail because the FR-09 surface of the source
modules declared in the SAB does not yet exist. The SAB binds FR-09 to:

    taskq_api.api.health   (the /healthz + /readyz probe endpoints)
    taskq_api.app          (composition root — wires the health router in)

The GREEN step will implement, on `taskq_api.api.health`:
    - ``GET /healthz`` — 200 ``{"status": "ok"}`` with NO
      ``WWW-Authenticate`` challenge (SPEC §3 FR-09 / FR-03 exception).
    - ``GET /readyz`` — 200 when both DB ping AND alembic current == head;
      503 ``application/problem+json`` otherwise, with a ``detail`` that
      mentions ``db`` (DB down) or ``migration`` (alembic behind head)
      so the operator can grep for which check failed (SPEC §8 #10, #11).

Per the TEST_INVENTORY / TEST_SPEC catalog (FR-09), the four test
functions below cover the canonical acceptance criteria:

    AC1-healthz-status          GET /healthz -> 200
    AC1-no-auth-required        no WWW-Authenticate header on /healthz
    AC2-readyz-ok-status        GET /readyz -> 200 when DB up + head
    AC3-readyz-down-status      GET /readyz -> 503 when DB down
    AC3-detail-db               detail body contains "db"
    AC4-readyz-migration-status GET /readyz -> 503 when alembic behind head
    AC4-detail-migration        detail body contains "migration"

The TEST_SPEC names are the contract; spec-coverage-check uses exact
match. Do NOT rename these functions.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Top-level imports — RED will surface as ModuleNotFoundError for the
# FR-09 surface (the new ``taskq_api.api.health`` module the SAB binds
# this FR to) when the GREEN step has not yet landed. It is EXPECTED
# and acceptable for pytest to fail with Collection Error (Exit Code 2)
# at this stage.
#
# We import the SAB-declared module so the GREEN step is forced to land
# the implementation at this exact path (Gate 1's Architecture Amendment
# Protocol BLOCKS phantom modules).
from taskq_api.api.health import healthz, readyz  # noqa: F401
from taskq_api.app import create_app  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alembic_db(db_path: Path, version_num: str | None) -> None:
    """Materialise a sqlite file with an ``alembic_version`` row.

    Used by the /readyz happy-path and behind-head tests so the
    GREEN-step alembic probe can read back the same row it would read
    against a real deployment. ``version_num=None`` initialises an
    empty ``alembic_version`` table (the "no row" branch).
    """
    from sqlalchemy import create_engine as _real_create_engine

    engine = _real_create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        if version_num is not None:
            conn.exec_driver_sql(
                "INSERT INTO alembic_version (version_num) VALUES (:v)",
                {"v": version_num},
            )


def _exploding_create_engine(*_args, **_kwargs):
    """Stand-in for ``sqlalchemy.create_engine`` that always raises.

    Lets the behind-head / DB-down tests assert the /readyz probe
    fail-closes without actually needing the DB to disappear.
    """
    raise RuntimeError("simulated DB unreachable")


# ---------------------------------------------------------------------------
# Test cases from TEST_SPEC.md (do NOT rename)
# ---------------------------------------------------------------------------


# NFR-03 NFR-05 NFR-09 NFR-10
def test_fr09_healthz_no_auth():
    """AC1-healthz-status / AC1-no-auth-required. [FR-09][SPEC §3]

    ``GET /healthz`` MUST return 200 with ``{"status": "ok"}`` AND MUST
    NOT challenge the caller for credentials (no ``WWW-Authenticate``
    header). SPEC §3 FR-09 / FR-03 explicitly carves /healthz out of
    the auth dependency chain.

    GREEN TODO: ``taskq_api.api.health.healthz`` must be an
    awaitable returning ``{"status": "ok"}`` that the FastAPI app
    mounts at ``GET /healthz`` with NO auth dependency.
    """
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    app = FastAPI()
    app.add_api_route(
        "/healthz",
        healthz,
        methods=["GET"],
        # No dependencies=[] override needed; healthz itself must not
        # raise HTTPException(401) and must not surface a challenge.
    )

    transport = ASGITransport(app=app)
    import asyncio

    async def _call() -> None:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.get("/healthz", headers={"X-API-Key": ""})
            result_status_code = resp.status_code
            result_header_www_authenticate = resp.headers.get("WWW-Authenticate")
            result_body = resp.json()

            assert result_status_code == 200
            assert result_header_www_authenticate is None
            assert result_body == {"status": "ok"}

    asyncio.get_event_loop().run_until_complete(_call())


# NFR-03 NFR-05 NFR-09 NFR-10
def test_fr09_readyz_200_when_ok(monkeypatch, tmp_path):
    """AC2-readyz-ok-status. [FR-09][SPEC §3][SPEC §8 #10, #11]

    When the DB ping succeeds AND ``alembic current`` equals the
    configured head revision, ``GET /readyz`` MUST return 200. SPEC
    §8 #10, #11.

    GREEN TODO: ``taskq_api.api.health.readyz`` must invoke the
    alembic probe (``SELECT version_num FROM alembic_version``) AND
    a DB-ping helper; when both succeed and the revision matches the
    configured head, it must return a 200 JSON body whose shape is
    what the composition root mounts on ``GET /readyz``.
    """
    from httpx import ASGITransport, AsyncClient

    db_path = tmp_path / "readyz_ok.sqlite"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    # ``v3_split_results`` matches ``app._MIGRATION_HEAD`` so the
    # readyz probe reports ``is_at_head=True``.
    _make_alembic_db(db_path, version_num="v3_split_results")

    app = create_app()
    transport = ASGITransport(app=app)
    import asyncio

    async def _call() -> None:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.get("/readyz")
            result_status_code = resp.status_code

            assert result_status_code == 200

    asyncio.get_event_loop().run_until_complete(_call())


# NFR-03 NFR-05 NFR-09 NFR-10
def test_fr09_readyz_503_when_db_down(monkeypatch):
    """AC3-readyz-down-status / AC3-detail-db. [FR-09][SPEC §8 #10]

    When the DB is unreachable, ``GET /readyz`` MUST return 503 with
    a ``detail`` mentioning ``db`` so the operator can identify which
    probe failed (SPEC §8 #10).

    GREEN TODO: ``taskq_api.api.health.readyz`` must fail closed
    (return a 503 ``application/problem+json`` envelope whose
    ``detail`` contains ``db``) when the alembic probe cannot reach
    the configured DB. The composition root must also surface this
    503 on ``GET /readyz`` (the test exercises the live route so
    SAB-declared ``taskq_api.app`` is on the test path too).
    """
    from httpx import ASGITransport, AsyncClient

    # Point at a sqlite URL whose ``create_engine`` call we then blow
    # up — exercises the DB-down branch of ``_check_migration_state``
    # without requiring a real Postgres outage.
    monkeypatch.setenv("TASKQ_DB_URL", "sqlite:///db-down-probe.sqlite")
    monkeypatch.setattr("taskq_api.app.create_engine", _exploding_create_engine)

    app = create_app()
    transport = ASGITransport(app=app)
    import asyncio

    async def _call() -> None:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.get("/readyz")
            result_status_code = resp.status_code
            result_problem_detail_str = resp.text

            assert result_status_code == 503
            assert "db" in result_problem_detail_str

    asyncio.get_event_loop().run_until_complete(_call())


# NFR-03 NFR-05 NFR-09 NFR-10
def test_fr09_readyz_503_when_alembic_not_head(monkeypatch, tmp_path):
    """AC4-readyz-migration-status / AC4-detail-migration. [FR-09][SPEC §8 #11]

    When the alembic revision is BEHIND head (e.g. an operator ran
    ``alembic downgrade -1`` but forgot to redeploy with the new
    schema), ``GET /readyz`` MUST return 503 with a ``detail``
    mentioning ``migration`` — fail-closed so traffic is NOT routed
    to a process whose schema does not match the migrations (SPEC
    §8 #11).

    GREEN TODO: ``taskq_api.api.health.readyz`` (via the
    composition-root ``app._check_migration_state`` probe) must
    return 503 ``application/problem+json`` whose ``detail``
    contains the substring ``migration`` whenever the
    ``alembic_version.version_num`` row does not match the
    configured head.
    """
    from httpx import ASGITransport, AsyncClient

    db_path = tmp_path / "readyz_behind.sqlite"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    # ``v1_initial`` is the original revision; the configured head is
    # ``v3_split_results`` so the probe reports ``is_at_head=False``.
    _make_alembic_db(db_path, version_num="v1_initial")

    app = create_app()
    transport = ASGITransport(app=app)
    import asyncio

    async def _call() -> None:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.get("/readyz")
            result_status_code = resp.status_code
            result_problem_detail_str = resp.text

            assert result_status_code == 503
            assert "migration" in result_problem_detail_str

    asyncio.get_event_loop().run_until_complete(_call())


# ---------------------------------------------------------------------------
# Coverage tests — exercise the SPEC-required branches of the FR-09 surface
# that the four canonical cases do not touch. Each test cites the source
# line(s) it covers so a future audit can drop or extend individual cases.
# ---------------------------------------------------------------------------


# NFR-03
def test_fr09_readyz_module_level_unwired_probe_returns_503():
    """Coverage — health.py:104. The module-level ``readyz`` always
    delegates to ``_unwired_probe`` (the composition root is what wires
    the real probe via ``create_health_router``); a direct call against
    the importable handler returns the 503 ``application/problem+json``
    envelope whose detail names the unwired state.
    """
    import taskq_api.api.health as _health_mod
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    app = FastAPI()
    app.add_api_route(
        "/readyz",
        _health_mod.readyz,
        methods=["GET"],
    )

    transport = ASGITransport(app=app)
    import asyncio

    async def _call() -> None:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.get("/readyz")
            assert resp.status_code == 503
            assert "application/problem+json" in resp.headers["content-type"]
            assert "migration state unknown" in resp.text

    asyncio.get_event_loop().run_until_complete(_call())


# NFR-03
def test_fr09_unwired_probe_health_lines(monkeypatch, tmp_path):
    """Coverage — health.py:34, 44. Direct unit-test of ``_unwired_probe``
    asserts the fail-closed default the module-level ``readyz`` delegates
    to when ``create_health_router`` was never installed.
    """
    from taskq_api.api.health import _unwired_probe

    is_at_head, detail_str = _unwired_probe()
    assert is_at_head is False
    assert "migration state unknown" in detail_str
    assert "readyz probe not wired" in detail_str


# NFR-03
def test_fr09_readyz_503_when_no_db_url(monkeypatch):
    """Coverage — app.py:108. With ``TASKQ_DB_URL`` unset, ``/readyz``
    fails closed with a 503 whose detail names the missing configuration
    so an operator can grep for ``unknown``.
    """
    from httpx import ASGITransport, AsyncClient

    monkeypatch.delenv("TASKQ_DB_URL", raising=False)
    from taskq_api.app import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    import asyncio

    async def _call() -> None:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.get("/readyz")
            assert resp.status_code == 503
            assert "unknown" in resp.text
            assert "TASKQ_DB_URL" in resp.text

    asyncio.get_event_loop().run_until_complete(_call())


# NFR-03
def test_fr09_readyz_503_when_alembic_table_empty(monkeypatch, tmp_path):
    """Coverage — app.py:129. The ``alembic_version`` table exists but is
    unstamped; the probe reports a behind-head state with ``migration``
    in the detail (SPEC §8 #11 — confirmed migration gap, not a DB fault).
    """
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import create_engine as _real_create_engine

    db_path = tmp_path / "alembic_unstamped.sqlite"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")

    engine = _real_create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        # No INSERT — the table exists but is empty.

    from taskq_api.app import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    import asyncio

    async def _call() -> None:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.get("/readyz")
            assert resp.status_code == 503
            assert "migration" in resp.text
            assert "no row" in resp.text

    asyncio.get_event_loop().run_until_complete(_call())


# NFR-03
def test_fr09_metrics_endpoint_redacts_password(monkeypatch):
    """Coverage — app.py:155-163, 272-273. ``GET /v1/metrics`` returns the
    Prometheus body whose DB URL line has its password fragment redacted
    (NFR-04 / SPEC §8 #20).
    """
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setenv("TASKQ_DB_URL", "postgresql://u:secretpw@host.example/db")
    from taskq_api.app import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    import asyncio

    async def _call() -> None:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.get("/v1/metrics")
            assert resp.status_code == 200
            body = resp.text
            assert "taskq_db_url" in body
            assert "secretpw" not in body
            assert "# HELP taskq_db_url" in body
            assert "# TYPE taskq_db_url" in body

    asyncio.get_event_loop().run_until_complete(_call())


# NFR-03
def test_fr09_build_metrics_body_direct(monkeypatch):
    """Coverage — app.py:155-163. Direct unit-test of ``_build_metrics_body``
    so the dict-join code path is exercised even if the route layer is
    bypassed by future refactors.
    """
    monkeypatch.setenv("TASKQ_DB_URL", "postgresql://u:secret@host/db")
    from taskq_api.app import _build_metrics_body

    body = _build_metrics_body()
    assert "taskq_db_url" in body
    assert "secret" not in body
    assert "# HELP" in body
    assert "# TYPE" in body
    assert body.endswith("\n")


# NFR-03
def test_fr09_metrics_body_handles_missing_db_url(monkeypatch):
    """Coverage — app.py:155-156. With ``TASKQ_DB_URL`` unset, the
    metrics body still renders, with the redacted-URL slot empty.
    """
    monkeypatch.delenv("TASKQ_DB_URL", raising=False)
    from taskq_api.app import _build_metrics_body

    body = _build_metrics_body()
    assert "taskq_db_url ''" in body


# NFR-03
def test_fr09_flat_include_router_nested_recursion():
    """Coverage — app.py:185-186. ``_flat_include_router`` recurses into
    ``_IncludedRouter.original_router`` so nested includes also land on
    ``app.routes`` directly (SPEC §3 FR-04 single-dependency invariant).
    """
    from fastapi import FastAPI
    from fastapi.routing import APIRouter

    from taskq_api.app import _flat_include_router

    app = FastAPI()
    inner_router = APIRouter()

    @inner_router.get("/coverage-inner-leaf")
    async def _inner_leaf():  # pragma: no cover - stub for routing only
        return {"ok": True}

    outer_router = APIRouter()
    outer_router.include_router(inner_router)

    _flat_include_router(app, outer_router)

    paths = [r.path for r in app.routes]
    assert "/coverage-inner-leaf" in paths


# NFR-03
def test_fr09_lifespan_runs_graceful_drain(monkeypatch):
    """Coverage — app.py:205-215. The composition-root lifespan enters
    ``TaskRunner`` construction and runs ``shutdown(drain_timeout_seconds)``
    on exit so FR-08 graceful-drain is observable from the FR-09 wiring
    path (``create_app`` is the same code that mounts the health router).
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
    import asyncio

    async def _enter_then_exit() -> None:
        async with lifespan_cm(app):
            pass

    asyncio.get_event_loop().run_until_complete(_enter_then_exit())

    assert shutdown_called_holder["called"] is True
    assert isinstance(shutdown_called_holder["drain_timeout"], float)


# NFR-03
def test_fr09_lifespan_awaits_async_shutdown(monkeypatch):
    """Coverage — app.py:214-215. When ``runner.shutdown`` returns a
    coroutine (async mock), the lifespan ``await``s it on exit so FR-08
    graceful drain does not silently drop the async drain.
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
    import asyncio

    async def _enter_then_exit() -> None:
        async with lifespan_cm(app):
            pass

    asyncio.get_event_loop().run_until_complete(_enter_then_exit())

    assert await_count_holder["count"] == 1
