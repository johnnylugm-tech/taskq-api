"""Tests for the [SAB] ``taskq_api.config`` independence module.

Citations:
- SAB.json — declares a 'config' layer with module 'taskq_api.config'
  and ``allowed_dependencies: []``.
- SAD.md §2.4 — architecture constraint
  ``errors_and_config_are_independence_modules`` — config reads the
  process environment directly and depends on no other
  ``taskq_api.*`` modules.
- SRS.md §2 — directory listing declares ``taskq_api/config.py`` as
  the home for the ``TASKQ_*`` env surface.

These tests pin the typed accessor contracts so refactors stay honest:
each accessor returns the documented default when the env var is
unset/empty, coerces when set, and is overridable per-call.
"""
from __future__ import annotations

from taskq_api import config


# ---------------------------------------------------------------------------
# Env-var name constants (SSOT for the public env surface)
# ---------------------------------------------------------------------------


def test_config_env_var_name_constants_match_srs():
    """The TASKQ_* env-var name constants are the SSOT used everywhere.

    Pins the spelling so accidental renames in app.py / deps.py /
    runner.py are caught here first.
    """
    assert config.TASKQ_DB_URL == "TASKQ_DB_URL"
    assert config.TASKQ_DRAIN_TIMEOUT == "TASKQ_DRAIN_TIMEOUT"
    assert config.TASKQ_MAX_CONCURRENT == "TASKQ_MAX_CONCURRENT"
    assert config.TASKQ_RATE_BURST == "TASKQ_RATE_BURST"
    assert config.TASKQ_RATE_PER_SEC == "TASKQ_RATE_PER_SEC"
    assert config.TASKQ_TASK_TIMEOUT == "TASKQ_TASK_TIMEOUT"


def test_config_dunder_all_lists_public_surface():
    """`__all__` enumerates exactly the names we documented as public."""
    expected = {
        "TASKQ_DB_URL",
        "TASKQ_DRAIN_TIMEOUT",
        "TASKQ_MAX_CONCURRENT",
        "TASKQ_RATE_BURST",
        "TASKQ_RATE_PER_SEC",
        "TASKQ_TASK_TIMEOUT",
        "get_db_url",
        "get_drain_timeout_seconds",
        "get_max_concurrent",
        "get_task_timeout_seconds",
    }
    assert set(config.__all__) == expected


# ---------------------------------------------------------------------------
# get_db_url
# ---------------------------------------------------------------------------


def test_get_db_url_returns_empty_default_when_unset(monkeypatch):
    """``get_db_url()`` returns the default ('') when TASKQ_DB_URL is unset."""
    monkeypatch.delenv("TASKQ_DB_URL", raising=False)
    assert config.get_db_url() == ""


def test_get_db_url_returns_configured_value(monkeypatch):
    """``get_db_url()`` returns the env value when set."""
    monkeypatch.setenv("TASKQ_DB_URL", "postgresql://u:p@host/db")
    assert config.get_db_url() == "postgresql://u:p@host/db"


def test_get_db_url_respects_explicit_default(monkeypatch):
    """``get_db_url(default=...)`` is honoured when the env is unset."""
    monkeypatch.delenv("TASKQ_DB_URL", raising=False)
    assert config.get_db_url(default="sqlite:///fallback.db") == "sqlite:///fallback.db"


# ---------------------------------------------------------------------------
# get_drain_timeout_seconds
# ---------------------------------------------------------------------------


def test_get_drain_timeout_default_when_unset(monkeypatch):
    """Default is 5.0 seconds per SPEC §3 FR-08 (drain_timeout default)."""
    monkeypatch.delenv("TASKQ_DRAIN_TIMEOUT", raising=False)
    assert config.get_drain_timeout_seconds() == 5.0


def test_get_drain_timeout_coerces_float(monkeypatch):
    """String env value is coerced to float."""
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "12.5")
    assert config.get_drain_timeout_seconds() == 12.5


def test_get_drain_timeout_treats_empty_as_unset(monkeypatch):
    """Empty string is treated like unset → default kicks in."""
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "")
    assert config.get_drain_timeout_seconds() == 5.0


def test_get_drain_timeout_respects_explicit_default(monkeypatch):
    """Caller-supplied default is honoured when the env is unset."""
    monkeypatch.delenv("TASKQ_DRAIN_TIMEOUT", raising=False)
    assert config.get_drain_timeout_seconds(default=1.5) == 1.5


# ---------------------------------------------------------------------------
# get_max_concurrent
# ---------------------------------------------------------------------------


def test_get_max_concurrent_default_when_unset(monkeypatch):
    """Default is 2 per SPEC §3 FR-08 (max_concurrent default)."""
    monkeypatch.delenv("TASKQ_MAX_CONCURRENT", raising=False)
    assert config.get_max_concurrent() == 2


def test_get_max_concurrent_coerces_int(monkeypatch):
    """String env value is coerced to int."""
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "8")
    assert config.get_max_concurrent() == 8


def test_get_max_concurrent_treats_empty_as_unset(monkeypatch):
    """Empty string is treated like unset → default kicks in."""
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "")
    assert config.get_max_concurrent() == 2


# ---------------------------------------------------------------------------
# get_task_timeout_seconds
# ---------------------------------------------------------------------------


def test_get_task_timeout_default_when_unset(monkeypatch):
    """Default is 30.0 seconds (FR-08 task-cap default)."""
    monkeypatch.delenv("TASKQ_TASK_TIMEOUT", raising=False)
    assert config.get_task_timeout_seconds() == 30.0


def test_get_task_timeout_coerces_float(monkeypatch):
    """String env value is coerced to float."""
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "45.0")
    assert config.get_task_timeout_seconds() == 45.0


def test_get_task_timeout_treats_empty_as_unset(monkeypatch):
    """Empty string is treated like unset → default kicks in."""
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "")
    assert config.get_task_timeout_seconds() == 30.0


# ---------------------------------------------------------------------------
# Independence-module contract (no internal taskq_api.* imports)
# ---------------------------------------------------------------------------


def test_config_module_has_no_taskq_internal_imports():
    """``config`` is an independence module — it must not import any other
    ``taskq_api.*`` module (SAD §2.4 / SAB.json ``allowed_dependencies: []``).

    Inspects ``config.__dict__`` rather than ``ast`` so we catch both
    ``import taskq_api.x`` and ``from taskq_api.x import ...`` shapes.
    """
    import taskq_api.config as cfg

    offenders = {
        name
        for name in cfg.__dict__
        if name.startswith("taskq_api") and name != "taskq_api.config"
    }
    assert not offenders, (
        "config is an independence module; found internal taskq_api imports: "
        + ", ".join(sorted(offenders))
    )
