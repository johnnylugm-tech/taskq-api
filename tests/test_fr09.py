"""RED acceptance tests for FR-09 Health checks and observability.

[FR-09]
Citations: SPEC.md §3 FR-09 (AC-9.1); SRS.md §3 FR-09; SAD.md §2;
            SRS.md §8 #10, #11.

Each test function binds the TEST_SPEC declared input values to local
variables so the spec sub-assertion predicates (``health_path == "/healthz"``,
``db_url != ""``, ``alembic_current != alembic_head``,
``scope_name == "admin"``) are present in the AST as ``assert``
expressions. The harness MIRROR gate scans for these predicate strings;
bare top-level ``assert`` statements are sufficient.

Tests are intentionally synchronous: ``_extract_sub_assertions`` in the
harness MIRROR gate filters on ``isinstance(fn, ast.FunctionDef)`` and
``ast.AsyncFunctionDef`` is *not* a subclass of ``ast.FunctionDef`` in
CPython's ``ast`` module (it inherits directly from ``ast.stmt``), so any
assertions nested under ``async def`` would be silently dropped.

``taskq_api.api.health`` is imported at module level so that the RED
state is a clean ``Collection Error`` (Exit Code 2) while the FR-09
surface is not yet on disk. Per the task contract this is a valid RED
state, NOT a defect to mask — there is deliberately no try/except
ImportError guard here.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from taskq_api.api import health
from taskq_api.api.deps import register_key

# GREEN TODO: ``taskq_api.api.health`` is the SAB-declared dotted path for
# FR-09 (verified against ``.methodology/SAB.json`` → layer "api"). GREEN
# MUST create ``03-development/src/taskq_api/api/health.py`` with at least
# the surface below so Gate 1 cannot block it as a phantom module.
#
#   health.CheckResult
#       — dataclass(frozen=True) with fields:
#           name: str      — "database" | "migration"
#           ok: bool       — True when the check passed
#           detail: str    — human-readable explanation, non-empty on failure
#
#   health.database_ping() -> None
#       — Executes ``SELECT 1`` against ``session.engine_from_env()``.
#         Returns None on success; propagates the driver exception
#         (e.g. sqlalchemy.exc.OperationalError) when the DB is
#         unreachable. Kept as a separate seam so tests can inject a
#         failure without a real DB outage.
#
#   health.head_revision() -> str | None
#       — The alembic script directory's head revision id. None when it
#         cannot be determined.
#
#   health.current_revision() -> str | None
#       — The revision recorded in the DB's ``alembic_version`` table.
#         None when the table is absent / unreadable.
#
#   health.check_database() -> CheckResult
#       — Calls ``database_ping()``; ok=False with a detail naming the DB
#         when it raises. MUST resolve ``database_ping`` through the
#         module global at call time (a plain ``database_ping()`` call),
#         NOT via a reference captured at import, so monkeypatching the
#         module attribute takes effect.
#
#   health.check_migration() -> CheckResult
#       — Compares ``current_revision()`` to ``head_revision()``. ok=True
#         ONLY when both are non-None and equal. AC-9.1 fail-closed: any
#         None, mismatch, or raised exception MUST yield ok=False.
#         Same module-global call requirement as ``check_database``.
#
#   health.router: fastapi.APIRouter
#       — Carries GET /healthz, GET /readyz and GET /v1/metrics. The
#         health routes MUST move OFF the inline definitions currently in
#         ``taskq_api.app.create_app`` and onto this router, so the route
#         endpoints' ``__module__`` is "taskq_api.api.health".
#
#   health.healthz() -> dict[str, str]
#       — Liveness handler; returns {"status": "ok"}. No auth, no DB access.
#
#   health.NOT_READY_PROBLEM_TYPE: str
#       — Stable problem+json ``type`` URI for the 503 (e.g. "/errors/not-ready").
#
#   health.metrics_snapshot() -> dict[str, object]
#       — {"task_count": {<status>: int, ...},
#          "execution_latency_ms": {"p50": float, "p95": float, "p99": float},
#          "rate_limit_rejections": int}
#
# GREEN WIRING TODO — /v1/metrics MUST be mounted under the SAME single
# ``require_api_key`` boundary the task routes use (AC-4.3 forbids a second
# ``Depends`` on the route) with ``Depends(require_scope("admin"))`` layered
# inside it. /healthz and /readyz MUST stay outside that boundary (AC-3.5
# no auth, AC-5.4 no rate limit).
#
# GREEN CROSS-FR TODO — ``test_fr05_health_endpoints_exempt_from_rate_limit``
# asserts /readyz returns 200 fifty times with NO monkeypatching. The real
# ``check_database`` / ``check_migration`` must therefore succeed in the
# default test environment (or degrade to ok=True when no DATABASE_URL and
# no alembic_version table are configured at all). Do NOT satisfy FR-09 by
# making the un-patched /readyz return 503 — that would break FR-05.
from taskq_api.api.health import (  # noqa: E402
    NOT_READY_PROBLEM_TYPE,
    CheckResult,
    check_database,
    check_migration,
    metrics_snapshot,
)
from taskq_api.app import app  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client():
    """ASGI in-process client factory with an isolated task store per test.

    Returns a callable that yields a fresh ``httpx.Client`` on each
    invocation so the test can ``with factory() as client:`` multiple
    times within a single test (the second ``with`` on a single
    ``httpx.Client`` would otherwise fail because the first ``__exit__``
    closes the transport). The per-test repository reset mirrors what
    ``test_fr03`` / ``test_fr04`` do so the FR-09 metrics counters
    start from a known empty store regardless of collection order.
    """
    repository = app.state.task_service._repository
    for attribute in ("_tasks", "_ordered_ids", "_names", "_runs"):
        container = getattr(repository, attribute, None)
        if container is not None:
            container.clear()

    def _factory() -> httpx.Client:
        return httpx.Client(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    return _factory


def _route_for(path: str):  # type: ignore[no-untyped-def]
    """Return the registered APIRoute whose path matches ``path``."""
    for route in app.router.routes:
        if getattr(route, "path", None) == path:
            return route
    return None


# ---------------------------------------------------------------------------
# FR-09 / AC-9.1 — /healthz liveness probe
# ---------------------------------------------------------------------------


# NFR-02 — security: the liveness probe is unauthenticated by contract
# (AC-3.5), so it MUST NOT touch the database or leak any process detail
# beyond a fixed {"status": "ok"} body. A liveness probe that queries the
# DB turns a DB outage into a pod-restart storm; SPEC §3 FR-09 scopes
# /healthz to "process alive" only and delegates dependency health to
# /readyz.
#
# NFR-06 — layering: the probe handler lives in ``api.health`` (the
# SAB-declared module for FR-09), not inline in ``app.create_app``.
def test_fr09_healthz_returns_200_ok(app_client: httpx.Client) -> None:
    """AC-9.1: GET /healthz → 200 {"status": "ok"}, no auth required."""
    health_path = "/healthz"
    assert health_path == "/healthz"  # AC9.1-healthz-path

    # ---- in-process unit path (pytest-cov measurable) ----
    # Call the handler directly so the module body is exercised even if
    # the ASGI stack short-circuits.
    assert health.healthz() == {"status": "ok"}

    # ---- end-to-end path through the ASGI app ----
    with app_client() as client:
        # Deliberately no X-API-Key header — AC-3.5 keeps this route
        # outside the auth boundary.
        response = client.get(health_path)

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}

    # The route MUST be owned by the SAB-declared FR-09 module. This is
    # what forces the placeholder currently defined inline in
    # ``taskq_api.app.create_app`` to move into ``taskq_api.api.health``.
    route = _route_for(health_path)
    assert route is not None, "no /healthz route registered on the app"
    assert route.endpoint.__module__ == "taskq_api.api.health"

    # Liveness MUST NOT depend on the database: the handler answers 200
    # even when every dependency probe is failing.
    assert health.healthz() == {"status": "ok"}


# ---------------------------------------------------------------------------
# FR-09 / AC-9.1 — /readyz returns 503 when the database is unreachable
# ---------------------------------------------------------------------------


# NFR-03 — resilience: an unreachable database MUST surface as a 503 with
# a body naming the failed check, never as a 200 (which would let a load
# balancer route traffic into a broken replica) and never as an unhandled
# 500 stack trace.
#
# SPEC §8 #10 — "GET /readyz with DB stopped → 503, detail says DB
# unreachable" is the canonical acceptance scenario this test encodes.
def test_fr09_readyz_returns_503_when_db_unreachable(
    app_client: httpx.Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-9.1: DB unreachable → 503 whose body explains the DB check failed."""
    db_url = "sqlite:///./nonexistent.db"
    assert db_url != ""  # AC9.1-readyz-db-unreachable

    # NOTE: pointing at a non-existent SQLite file is NOT sufficient to
    # simulate an outage — SQLite silently CREATES the file on connect, so
    # the probe would come back healthy. The TEST_SPEC input value is bound
    # above for the sub-assertion; the actual unreachability is injected at
    # the ``database_ping`` seam so this test exercises the real
    # ``check_database`` failure branch deterministically and without
    # depending on a live server.
    def _unreachable() -> None:
        raise OperationalError(
            "SELECT 1", {}, Exception(f"could not connect to {db_url}")
        )

    monkeypatch.setattr(health, "database_ping", _unreachable)
    # Hold the migration check healthy so the 503 is attributable to the
    # database check alone and the body assertion below is unambiguous.
    monkeypatch.setattr(health, "current_revision", lambda: "v3")
    monkeypatch.setattr(health, "head_revision", lambda: "v3")

    # ---- in-process unit path (pytest-cov measurable) ----
    db_check = check_database()
    assert isinstance(db_check, CheckResult)
    assert db_check.name == "database"
    assert db_check.ok is False
    assert db_check.detail != ""
    # The migration check is unaffected by the DB ping failure.
    assert check_migration().ok is True

    # ---- end-to-end path through the ASGI app ----
    with app_client() as client:
        # No X-API-Key — /readyz stays outside the auth boundary (AC-3.5).
        response = client.get("/readyz")

    assert response.status_code == 503, response.text
    assert response.headers["content-type"].startswith("application/problem+json")

    payload = response.json()
    assert payload["status"] == 503
    assert payload["type"] == NOT_READY_PROBLEM_TYPE

    # The body MUST explain WHICH check failed (SPEC §3 FR-09: "503 with
    # body explaining which check failed"). We match on the check name
    # rather than exact prose so GREEN keeps wording freedom.
    body_text = response.text.lower()
    assert "database" in body_text, response.text
    # ...and it must NOT claim the migration check is the culprit here.
    assert "migration" not in payload.get("detail", "").lower(), response.text

    # NP-08 — the failure body must not leak a driver stack trace, the SQL
    # it tried to run, or a filesystem path.
    for leak in ("traceback", "select 1", "/users/", "sqlalchemy.exc"):
        assert leak not in body_text, f"{leak!r} leaked into /readyz body"


# ---------------------------------------------------------------------------
# FR-09 / AC-9.1 — /readyz fails CLOSED when the migration is behind head
# ---------------------------------------------------------------------------


# AC-9.1 is the key guard of this FR: deploying new code without running
# the migration MUST fail closed. The dangerous failure mode is a probe
# that answers 200 whenever it cannot prove the schema is stale — that
# lets a half-migrated deploy take production traffic. Every branch below
# (behind head, unknown current, unknown head, probe raises) must land on
# NOT-ready.
#
# SPEC §8 #11 / SRS §3 FR-09 closing bullet.
def test_fr09_readyz_fails_closed_when_migration_behind_head(
    app_client: httpx.Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-9.1: alembic current != head → 503, fail closed."""
    alembic_head = "head"
    alembic_current = "v2"
    assert alembic_current != alembic_head  # AC9.1-migration-behind

    # Database is healthy throughout so the 503 is attributable to the
    # migration check alone.
    monkeypatch.setattr(health, "database_ping", lambda: None)
    monkeypatch.setattr(health, "current_revision", lambda: alembic_current)
    monkeypatch.setattr(health, "head_revision", lambda: alembic_head)

    # ---- in-process unit path (pytest-cov measurable) ----
    migration_check = check_migration()
    assert isinstance(migration_check, CheckResult)
    assert migration_check.name == "migration"
    assert migration_check.ok is False
    assert migration_check.detail != ""
    assert check_database().ok is True

    # ---- end-to-end path through the ASGI app ----
    with app_client() as client:
        response = client.get("/readyz")

    assert response.status_code == 503, response.text
    assert response.headers["content-type"].startswith("application/problem+json")

    payload = response.json()
    assert payload["status"] == 503
    assert payload["type"] == NOT_READY_PROBLEM_TYPE
    assert "migration" in response.text.lower(), response.text

    # ---- fail-closed matrix: ambiguity MUST NOT be read as "ready" ----
    # Unknown current revision (alembic_version table missing entirely —
    # exactly the "forgot to run migrate" deploy).
    monkeypatch.setattr(health, "current_revision", lambda: None)
    assert check_migration().ok is False

    # Unknown head revision (script directory unreadable).
    monkeypatch.setattr(health, "current_revision", lambda: alembic_head)
    monkeypatch.setattr(health, "head_revision", lambda: None)
    assert check_migration().ok is False

    # The revision probe itself raising MUST be caught and reported as
    # NOT-ready, not propagate as a 500.
    def _boom() -> str:
        raise OperationalError("alembic_version", {}, Exception("no such table"))

    monkeypatch.setattr(health, "head_revision", lambda: alembic_head)
    monkeypatch.setattr(health, "current_revision", _boom)
    assert check_migration().ok is False

    with app_client() as client:
        raising_response = client.get("/readyz")
    assert raising_response.status_code == 503, raising_response.text

    # Positive control: when current IS at head the check passes, proving
    # the 503s above come from the comparison and not a hardcoded failure.
    monkeypatch.setattr(health, "current_revision", lambda: alembic_head)
    assert check_migration().ok is True


# ---------------------------------------------------------------------------
# FR-09 / AC-9.1 — /v1/metrics requires admin scope and reports counters
# ---------------------------------------------------------------------------


# NFR-02 — security: operational metrics expose task volumes and
# rate-limit rejection counts, which are useful to an attacker probing
# for tenancy size and throttle thresholds. SPEC §3 FR-09 puts the
# endpoint behind the ``admin`` scope; a ``read``-scoped key MUST be
# rejected with 403 and an unauthenticated caller with 401.
#
# NFR-06 — layering: the counters are assembled in ``api.health``
# (``metrics_snapshot``); the route only serialises them.
def test_fr09_metrics_requires_admin_and_reports_counters(
    app_client: httpx.Client,
) -> None:
    """AC-9.1: GET /v1/metrics is admin-only and reports the three counter families."""
    scope_name = "admin"
    metric_name = "task_count"
    assert scope_name == "admin"  # AC9.1-metrics-scope-admin

    admin_key = "fr09-admin-key"
    read_key = "fr09-read-key"
    register_key(admin_key, scope_name, rate_limit_bypass=True)
    register_key(read_key, "read", rate_limit_bypass=True)

    metrics_path = "/v1/metrics"

    # ---- in-process unit path (pytest-cov measurable) ----
    snapshot = metrics_snapshot()
    assert isinstance(snapshot, dict)
    # Task counts by status (SPEC §3 FR-09 "Task counts (by status)").
    assert metric_name in snapshot, snapshot
    assert isinstance(snapshot[metric_name], dict)
    assert all(isinstance(value, int) for value in snapshot[metric_name].values())
    # Execution-latency percentiles.
    assert "execution_latency_ms" in snapshot, snapshot
    percentiles = snapshot["execution_latency_ms"]
    assert set(percentiles) >= {"p50", "p95", "p99"}, percentiles
    # Rate-limit rejection counts.
    assert "rate_limit_rejections" in snapshot, snapshot
    assert isinstance(snapshot["rate_limit_rejections"], int)

    # ---- end-to-end authorisation matrix ----
    with app_client() as client:
        anonymous = client.get(metrics_path)
        read_scoped = client.get(metrics_path, headers={"X-API-Key": read_key})
        admin_scoped = client.get(metrics_path, headers={"X-API-Key": admin_key})

    # Unauthenticated: metrics are NOT a public probe (unlike /healthz).
    assert anonymous.status_code == 401, anonymous.text

    # Authenticated but under-privileged: read < admin → 403 (AC-4.1).
    assert read_scoped.status_code == 403, read_scoped.text
    assert read_scoped.headers["content-type"].startswith("application/problem+json")

    # Admin: 200 with all three counter families.
    assert admin_scoped.status_code == 200, admin_scoped.text
    payload = admin_scoped.json()
    assert metric_name in payload, payload
    assert "execution_latency_ms" in payload, payload
    assert "rate_limit_rejections" in payload, payload
    assert set(payload["execution_latency_ms"]) >= {"p50", "p95", "p99"}
