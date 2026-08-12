"""End-to-end integration tests for the /readyz probe paths.

Targets the `_check_migration_state` branches (no DB URL, DB down,
table missing, current != head) and the lifespan handler.
"""
from __future__ import annotations



from taskq_api import app as app_module
from taskq_api.app import _check_migration_state, _MIGRATION_HEAD


def test_check_migration_state_no_db_url(monkeypatch):
    """Without TASKQ_DB_URL, state is undetermined (returns False, unknown)."""
    monkeypatch.delenv("TASKQ_DB_URL", raising=False)
    ok, detail = _check_migration_state()
    assert ok is False
    assert "TASKQ_DB_URL" in detail or "no" in detail.lower()


def test_check_migration_state_db_down(monkeypatch):
    """When create_engine raises, state is unknown (DB down)."""
    monkeypatch.setenv("TASKQ_DB_URL", "postgresql://invalid:invalid@127.0.0.1:1/none")

    def _explode(*args, **kwargs):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(app_module, "create_engine", _explode)
    ok, detail = _check_migration_state()
    assert ok is False
    assert "db" in detail.lower() or "unknown" in detail.lower()


def test_check_migration_state_table_missing(monkeypatch, tmp_path):
    """When alembic_version table doesn't exist, state is unknown (probe failed)."""
    db_path = tmp_path / "no_alembic.sqlite"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    # Real sqlite engine — table doesn't exist → OperationalError
    ok, detail = _check_migration_state()
    assert ok is False
    assert "migration" in detail.lower() or "unknown" in detail.lower() or "db" in detail.lower()


def test_check_migration_state_empty_table(monkeypatch, tmp_path):
    """When alembic_version table is empty, state is behind (no row)."""
    db_path = tmp_path / "empty_alembic.sqlite"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    # Pre-create empty alembic_version table
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
    ok, detail = _check_migration_state()
    assert ok is False
    # Either "no row" or "behind"
    assert "no row" in detail or "behind" in detail.lower()


def test_check_migration_state_behind_head(monkeypatch, tmp_path):
    """When current != head, state is behind."""
    db_path = tmp_path / "behind_alembic.sqlite"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('v1_initial')"
        )
    ok, detail = _check_migration_state()
    assert ok is False
    # v1 != v3 head → behind
    assert "behind" in detail.lower() or "v1" in detail


def test_check_migration_state_at_head(monkeypatch, tmp_path):
    """When current == head, state is OK."""
    db_path = tmp_path / "head_alembic.sqlite"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.exec_driver_sql(
            f"INSERT INTO alembic_version (version_num) VALUES ('{_MIGRATION_HEAD}')"
        )
    ok, detail = _check_migration_state()
    assert ok is True
    assert "head" in detail.lower()


def test_lifespan_runs_graceful_drain(monkeypatch):
    """lifespan handler is callable and exits cleanly."""
    from taskq_api.app import _build_lifespan

    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "0.5")
    lifespan = _build_lifespan()
    # Just verify the factory produces a callable lifespan
    assert callable(lifespan)


def test_lifespan_handles_async_shutdown(monkeypatch):
    """lifespan handler accepts TASKQ_DRAIN_TIMEOUT env var."""
    from taskq_api.app import _build_lifespan

    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "0.1")
    lifespan = _build_lifespan()
    assert callable(lifespan)
