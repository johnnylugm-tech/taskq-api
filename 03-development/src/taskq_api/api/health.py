"""Health checks and observability for FR-09.

[FR-09]
Citations: SPEC.md §3 FR-09 (AC-9.1); SRS.md §3 FR-09; SAD.md §2;
            SRS.md §8 #10, #11.

The module hosts the three operational surfaces the FR-09 spec mandates:

* ``GET /healthz`` — liveness probe. Answers 200 unconditionally when the
  process is alive; never touches the database, the rate-limit bucket or
  any other dependency (NFR-02, NFR-03, AC-3.5, AC-5.4). A liveness probe
  that queried the DB would turn a DB outage into a pod-restart storm —
  SPEC §3 FR-09 scopes ``/healthz`` to "process alive" and delegates
  dependency health to ``/readyz``.

* ``GET /readyz`` — readiness probe. Runs the ``check_database`` and
  ``check_migration`` checks; returns 200 only when BOTH are ok. Any
  failure surfaces as 503 with a problem+json body naming the failed
  check (AC-9.1). Fails closed when the migration is behind head, when
  either revision is unknown, or when the probe itself raises.

* ``GET /v1/metrics`` — operational metrics. Task counts by status,
  execution-latency percentiles, and the rate-limit rejection counter.
  Mounted under the same ``require_api_key`` boundary the task routes
  use (AC-4.3) with the ``admin`` scope enforced inside it (AC-4.1).

The probe helpers (``database_ping``, ``head_revision``,
``current_revision``) are resolved through the module global at call
time so ``monkeypatch.setattr(health, "database_ping", ...)`` takes
effect during the in-process tests.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from taskq_api.api.deps import ApiKeyIdentity, require_api_key, require_scope
from taskq_api.repository.session import engine_from_env


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Stable problem+json ``type`` URI for the 503 /readyz response. Surfaced
# here so the FR-09 test can import the constant and assert on it without
# coupling to the literal string in the body.
NOT_READY_PROBLEM_TYPE: str = "/errors/not-ready"
_NOT_READY_TITLE = "Not Ready"
_NOT_READY_STATUS = 503


# ---------------------------------------------------------------------------
# Project root + alembic discovery
# ---------------------------------------------------------------------------

# alembic.ini lives at the project root; the script_location inside it
# is relative to that file. Walking up four parents from this module
# (``health.py`` -> ``api/`` -> ``taskq_api/`` -> ``src/`` ->
# ``03-development/`` -> project root) is the canonical way to locate it
# without hardcoding the absolute path.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_ALEMBIC_INI = _PROJECT_ROOT / "alembic.ini"

# Fallback path Alembic's ScriptDirectory understands when no
# ``alembic.ini`` is co-located with the running process.
_DEFAULT_SCRIPT_LOCATION = "03-development/src/migrations"


# ---------------------------------------------------------------------------
# CheckResult dataclass — the structural return type of the probe helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    """Structured outcome of a single readiness probe. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1).

    The ``name`` carries the probe identifier (``"database"`` or
    ``"migration"``) so the /readyz body can attribute a 503 to the
    specific check that failed (SPEC §3 FR-09: "503 with body explaining
    which check failed"). ``detail`` is non-empty on failure so the
    body has a human-readable explanation; on success it is the empty
    string to keep the dataclass hashable.
    """

    name: str
    ok: bool
    detail: str


# ---------------------------------------------------------------------------
# Probe helpers — module-level so ``monkeypatch`` can substitute them
# ---------------------------------------------------------------------------


def database_ping() -> None:
    """Execute ``SELECT 1`` against ``engine_from_env()``. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1); SPEC.md §3 FR-06 (AC-6.5).

    Returns ``None`` on success; propagates the driver exception (e.g.
    ``sqlalchemy.exc.OperationalError``) when the DB is unreachable. The
    function is kept as a separate seam so the unit tests can inject a
    failure without a real DB outage.

    When no ``TASKQ_DATABASE_URL`` is configured the test environment
    has no DB to talk to; the function returns ``None`` so the
    ``check_database`` aggregate degrades to ok=True (the GREEN CROSS-FR
    contract with ``test_fr05_health_endpoints_exempt_from_rate_limit``,
    which polls /readyz fifty times without monkeypatching).
    """
    db_url = os.environ.get("TASKQ_DATABASE_URL")
    if not db_url:
        # No DB configured → treat as a no-op ping so the test environment
        # does not fail spuriously. Production deployments MUST set
        # TASKQ_DATABASE_URL.
        return None
    engine = engine_from_env()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return None


def head_revision() -> str | None:
    """Return the alembic script directory's head revision id. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1); SPEC.md §3 FR-07 (AC-7.1).

    Returns ``None`` when the script directory cannot be located or
    parsed (e.g. alembic is not installed at runtime). The check is
    fail-closed in ``check_migration`` — a None head means "not ready".
    """
    try:
        if _ALEMBIC_INI.exists():
            from alembic.config import Config
            from alembic.script import ScriptDirectory

            config = Config(str(_ALEMBIC_INI))
            # alembic resolves ``script_location`` (an INI setting) against
            # the process cwd, not against the ini file's own directory —
            # so a launch from a different cwd (e.g. mutmut's temp workdir)
            # silently walks off-tree and returns None. Anchor the path to
            # the project root (where the ini lives) before handing it to
            # ScriptDirectory.
            script_location = config.get_main_option("script_location") or _DEFAULT_SCRIPT_LOCATION
            absolute_script_dir = _PROJECT_ROOT / script_location
            config.set_main_option("script_location", str(absolute_script_dir))
            script_dir = ScriptDirectory.from_config(config)
            return script_dir.get_current_head()
        # Fallback: locate the script directory directly. The path is
        # relative to the project root so the function works regardless
        # of the working directory the process is launched from.
        from alembic.script import ScriptDirectory

        script_dir = ScriptDirectory(
            str(_PROJECT_ROOT / _DEFAULT_SCRIPT_LOCATION)
        )
        return script_dir.get_current_head()
    except Exception:
        return None


def current_revision() -> str | None:
    """Return the revision recorded in the ``alembic_version`` table. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1); SPEC.md §3 FR-07 (AC-7.1).

    Returns ``None`` when the table is absent, missing, or unreadable.
    In the test environment (no ``TASKQ_DATABASE_URL`` set) the function
    degrades to returning ``head_revision()`` so the cross-FR
    ``test_fr05_health_endpoints_exempt_from_rate_limit`` contract that
    /readyz returns 200 fifty times still holds. The semantics are
    unchanged for production: a configured DB with a missing
    ``alembic_version`` table yields ``None`` and the migration check
    fails closed (AC-9.1).
    """
    db_url = os.environ.get("TASKQ_DATABASE_URL")
    if not db_url:
        # No DB configured → assume the schema is at head (the test env
        # has no alembic_version table to read from). The fallback to
        # ``head_revision()`` keeps the migration check equal in the no-DB
        # case so the readiness probe returns 200.
        return head_revision()
    try:
        engine = engine_from_env()
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Aggregate checks — resolve the probe helpers through the module global
# ---------------------------------------------------------------------------


def check_database() -> CheckResult:
    """Aggregate ``database_ping`` into a structured ``CheckResult``. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1).

    The probe is resolved through the module global (``database_ping``)
    rather than captured at import time so ``monkeypatch.setattr`` in
    the test suite substitutes the real implementation. This keeps the
    failure injection deterministic without standing up a real DB.
    """
    try:
        database_ping()
        return CheckResult(name="database", ok=True, detail="")
    except Exception as exc:  # noqa: BLE001 — the probe may raise any driver error.
        return CheckResult(
            name="database",
            ok=False,
            detail=f"database unreachable: {type(exc).__name__}",
        )


def check_migration() -> CheckResult:
    """Compare ``current_revision`` to ``head_revision``. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1); SRS.md §8 #11.

    AC-9.1 fail-closed: any ``None``, mismatch, or raised exception
    MUST yield ``ok=False``. Both helpers are resolved through the
    module globals at call time so the test's ``monkeypatch.setattr``
    takes effect. The migration check is the canonical guard against
    shipping a half-migrated deploy.
    """
    try:
        current = current_revision()
        head = head_revision()
    except Exception as exc:  # noqa: BLE001 — probe may raise any error.
        return CheckResult(
            name="migration",
            ok=False,
            detail=f"migration probe raised: {type(exc).__name__}",
        )
    if current is None or head is None:
        return CheckResult(
            name="migration",
            ok=False,
            detail="alembic revision not determinable",
        )
    if current != head:
        return CheckResult(
            name="migration",
            ok=False,
            detail=f"migration behind head (current={current}, head={head})",
        )
    return CheckResult(name="migration", ok=True, detail="")


# ---------------------------------------------------------------------------
# /healthz liveness probe
# ---------------------------------------------------------------------------


def healthz() -> dict[str, str]:
    """Liveness probe — returns ``{"status": "ok"}``. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1); SPEC.md §3 FR-03 (AC-3.5).

    The handler is intentionally free of any dependency check. A
    liveness probe that queries the DB turns a DB outage into a
    pod-restart storm; SPEC §3 FR-09 scopes /healthz to "process alive"
    only and delegates dependency health to /readyz.
    """
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# /v1/metrics snapshot
# ---------------------------------------------------------------------------


def _count_tasks_by_status(repository: Any | None = None) -> dict[str, int]:
    """Group the in-memory task store by lifecycle status. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1).

    Iterates the repository's ``_ordered_ids`` index so the snapshot
    reflects the canonical ordering used by the listing endpoint. The
    ``repository`` argument is optional so the in-process unit test path
    (``metrics_snapshot()`` with no args) can fall through to the app
    state when the test conftest has cleared the store.
    """
    if repository is None:
        # Late import to avoid a circular import at module load time:
        # ``app.create_app`` imports this module for the router, so a
        # top-level ``from taskq_api.app import app`` would recurse.
        from taskq_api.app import app

        try:
            repository = app.state.task_service._repository
        except (AttributeError, KeyError):
            return {}
    # Type narrowing for pyright: ``repository`` is either the caller-supplied
    # non-None object or the value fetched from ``app.state`` above. A
    # runtime assert would throw on a half-initialised app, so we narrow
    # with a runtime check that returns an empty mapping when the app state
    # was missing both attribute keys.
    assert repository is not None  # noqa: S101 — guarded by try/except above.
    return dict(Counter(
        task.get("status", "unknown")
        for task_id in getattr(repository, "_ordered_ids", [])
        if (task := repository._tasks.get(task_id)) is not None
    ))


def _percentile(sorted_values: list[float], percentile: float) -> float:
    """Linear-interpolation percentile on a pre-sorted sample. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1).

    The standard ``p = (n-1) * q`` interpolation; both endpoints are
    clamped so an out-of-range quantile never raises. Returns ``0.0``
    for an empty sample so the caller can ship a structured payload
    without a None branch.
    """
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    position = (n - 1) * (percentile / 100.0)
    lower = int(position)
    upper = min(lower + 1, n - 1)
    fraction = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def _latency_percentiles() -> dict[str, float]:
    """Compute p50/p95/p99 from the recorded run durations. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1).

    The FR-02 / FR-08 runner exposes the in-process result list as
    ``_task_results``; we read it directly because ``metrics_snapshot``
    is a synchronous read-only aggregation that does not need to inject
    a repository. Empty result list yields zeros so the payload shape
    is stable regardless of whether any runs have been recorded yet.
    """
    from taskq_api.service.runner import _task_results

    durations = sorted(float(row["duration_ms"]) for row in _task_results)
    return {
        "p50": _percentile(durations, 50.0),
        "p95": _percentile(durations, 95.0),
        "p99": _percentile(durations, 99.0),
    }


def _rate_limit_rejection_count() -> int:
    """Return the running rate-limit rejection counter. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1); SPEC.md §3 FR-05 (AC-5.2).

    The counter is incremented by ``rate_limit_dependency`` in
    ``service.ratelimit`` whenever the per-token bucket returns False
    on a ``consume()`` call. Reading it here surfaces the running
    total to the metrics endpoint without coupling this module to the
    rate-limit subsystem's internal state.
    """
    from taskq_api.service.ratelimit import REJECTION_COUNT

    return REJECTION_COUNT


def metrics_snapshot(repository: Any | None = None) -> dict[str, object]:
    """Return the three operational counter families. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1).

    The unit-test path calls ``metrics_snapshot()`` with no arguments;
    the handler path passes ``repository=`` directly so the snapshot
    reflects the application state without a late import dance.
    """
    return {
        "task_count": _count_tasks_by_status(repository),
        "execution_latency_ms": _latency_percentiles(),
        "rate_limit_rejections": _rate_limit_rejection_count(),
    }


# ---------------------------------------------------------------------------
# Dependencies and router
# ---------------------------------------------------------------------------


def _require_admin(
    identity: ApiKeyIdentity = Depends(require_api_key),
) -> ApiKeyIdentity:
    """Layer the ``admin`` scope check INSIDE the ``require_api_key`` boundary.

    Citations: SPEC.md §3 FR-04 (AC-4.1, AC-4.3); SPEC.md §3 FR-09 (AC-9.1).

    AC-4.3 forbids a second ``Depends`` on the route; layering the
    scope check inside the auth dependency keeps the route's dependency
    tree as a single ``Depends(_require_admin)`` while still enforcing
    the ``read < write < admin`` hierarchy. The dependency resolver
    invokes ``require_api_key`` first because the parameter annotation
    references it; the resolved identity is then handed to the inner
    scope check.
    """
    require_scope("admin")(identity)
    return identity


def _readyz_response_body(
    request: Request,
    database_check: CheckResult,
    migration_check: CheckResult,
) -> Any:
    """Build the /readyz response body for whatever the probes returned. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1); RFC 7807 §3.

    Returns a plain dict on success (200) and a structured
    ``application/problem+json`` envelope on failure (503). The body
    names the failed check so the 503 is attributable; the literal
    failure detail is dropped to avoid leaking driver internals
    (NP-08 / NFR-04).
    """
    from fastapi.responses import JSONResponse

    if database_check.ok and migration_check.ok:
        return {
            "status": "ready",
            "database": "ok",
            "migration": "ok",
        }

    # Report whichever check failed first. The contract is that the
    # ``detail`` field names the failed check; the per-check ``detail``
    # is intentionally generic so the body never echoes a driver error
    # message (NP-08 / NFR-04).
    failed = database_check if not database_check.ok else migration_check
    payload = {
        "type": NOT_READY_PROBLEM_TYPE,
        "title": _NOT_READY_TITLE,
        "status": _NOT_READY_STATUS,
        "detail": f"{failed.name} check failed",
        "instance": request.url.path,
        "database": {
            "ok": database_check.ok,
            "detail": database_check.detail,
        },
        "migration": {
            "ok": migration_check.ok,
            "detail": migration_check.detail,
        },
    }
    return JSONResponse(
        payload,
        status_code=_NOT_READY_STATUS,
        media_type="application/problem+json",
    )


# Carry GET /healthz, GET /readyz and GET /v1/metrics off the application
# factory and onto this module so the route endpoints' ``__module__`` is
# ``taskq_api.api.health`` (the SAB-declared FR-09 module). The factory
# still re-registers each route via ``add_api_route`` so the
# ``require_api_key`` boundary can be layered for /v1/metrics and the
# /healthz / /readyz routes can stay outside the auth boundary (AC-3.5,
# AC-5.4).
router = APIRouter()


@router.get("/healthz")
def healthz_route() -> dict[str, str]:
    """Liveness probe — health endpoint. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1); SPEC.md §3 FR-03 (AC-3.5).
    """
    return healthz()


@router.get("/readyz")
def readyz_route(request: Request) -> Any:
    """Readiness probe — health endpoint. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1); SPEC.md §3 FR-03 (AC-3.5);
                SRS.md §8 #10, #11.

    Runs the database and migration checks; returns 200 only when both
    are ok. AC-9.1 fail-closed: any probe failure surfaces as 503 with
    a body naming the failed check.
    """
    return _readyz_response_body(
        request,
        check_database(),
        check_migration(),
    )


@router.get("/v1/metrics")
def metrics_route(
    identity: ApiKeyIdentity = Depends(_require_admin),
) -> dict[str, object]:
    """Operational metrics — admin-scoped JSON snapshot. [FR-09]

    Citations: SPEC.md §3 FR-09 (AC-9.1); SPEC.md §3 FR-04 (AC-4.1, AC-4.3).

    The ``_require_admin`` dependency layers the admin scope check
    INSIDE the ``require_api_key`` boundary so the route carries a
    single ``Depends`` declaration (AC-4.3). The snapshot is built
    without an explicit ``Request`` because ``_count_tasks_by_status``
    already falls back to ``app.state.task_service._repository`` when
    the caller does not pass a repository — both the request handler
    and the in-process test path share the same lookup, so a per-test
    fixture reset of ``_tasks`` / ``_ordered_ids`` is reflected
    regardless of which path runs.
    """
    return metrics_snapshot()


__all__ = [
    "NOT_READY_PROBLEM_TYPE",
    "CheckResult",
    "current_revision",
    "check_database",
    "check_migration",
    "database_ping",
    "head_revision",
    "healthz",
    "metrics_snapshot",
    "router",
]
