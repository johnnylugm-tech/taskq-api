"""[SAB] ``taskq_api.config`` — TASKQ_* environment configuration.

Citations:
- SAD.md §2.3 — ``config`` is an independence module importable by any
  layer; it owns the env-var surface (``TASKQ_*``).
- SAD.md §2.4 — architecture constraint
  ``errors_and_config_are_independence_modules`` — config has zero
  internal dependencies on other ``taskq_api`` modules (it reads the
  process environment directly), mirroring ``errors.py``.
- SRS.md §2 — directory listing declares ``taskq_api/config.py`` as
  the home for ``TASKQ_*`` env handling.

This module is intentionally leaf-level: ``SAB.json`` declares
``allowed_dependencies: []`` for the ``config`` layer. Adopting
helpers here must not import from other ``taskq_api.*`` modules, so
the layer remains independently testable.
"""
from __future__ import annotations

import os


# ── Env-var name constants ────────────────────────────────────────────────────
# Centralising the names here (instead of inlining ``os.environ.get("...")``
# at every call site) lets a single grep verify the public env surface and
# keeps typos from spreading across modules.

TASKQ_DB_URL = "TASKQ_DB_URL"
TASKQ_DRAIN_TIMEOUT = "TASKQ_DRAIN_TIMEOUT"
TASKQ_MAX_CONCURRENT = "TASKQ_MAX_CONCURRENT"
TASKQ_RATE_BURST = "TASKQ_RATE_BURST"
TASKQ_RATE_PER_SEC = "TASKQ_RATE_PER_SEC"
TASKQ_TASK_TIMEOUT = "TASKQ_TASK_TIMEOUT"


# ── Typed accessors ──────────────────────────────────────────────────────────
# Each accessor returns the env value coerced to the documented type, falling
# back to a default when the variable is unset. Callers that need an
# opt-in sentinel (e.g. rate limiting) should read the raw name via ``os.environ``
# instead — see ``api/deps.py::_read_rate_config`` for that pattern.


def get_db_url(default: str = "") -> str:
    """Return ``TASKQ_DB_URL`` or *default* when unset/empty."""
    return os.environ.get(TASKQ_DB_URL, default)


def get_drain_timeout_seconds(default: float = 5.0) -> float:
    """Return ``TASKQ_DRAIN_TIMEOUT`` (seconds) as ``float``."""
    raw = os.environ.get(TASKQ_DRAIN_TIMEOUT)
    if raw is None or raw == "":
        return default
    return float(raw)


def get_max_concurrent(default: int = 2) -> int:
    """Return ``TASKQ_MAX_CONCURRENT`` as ``int`` (FR-08 worker pool size)."""
    raw = os.environ.get(TASKQ_MAX_CONCURRENT)
    if raw is None or raw == "":
        return default
    return int(raw)


def get_task_timeout_seconds(default: float = 30.0) -> float:
    """Return ``TASKQ_TASK_TIMEOUT`` (seconds) as ``float`` (FR-08 task cap)."""
    raw = os.environ.get(TASKQ_TASK_TIMEOUT)
    if raw is None or raw == "":
        return default
    return float(raw)


__all__ = [
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
]
