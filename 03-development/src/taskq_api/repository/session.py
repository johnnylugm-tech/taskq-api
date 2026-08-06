"""SQLAlchemy engine and session boundary for FR-06.

[FR-06]
Citations: SPEC.md §3 FR-06 (AC-6.1..AC-6.5); SRS.md §3 FR-06;
            SAD.md §2.3.7, §3.1.

The repository layer is the sole owner of the SQLAlchemy ``Session``
lifecycle (AC-6.1). Every API request runs inside a single
``session_scope`` context manager that commits on clean exit and rolls
back when the with-block raises (AC-6.2). Connection pooling follows
SPEC §5.1 with ``pool_size=TASKQ_DB_POOL_SIZE`` and
``pool_pre_ping=True`` (AC-6.5).

The engine factory defaults to :class:`sqlalchemy.pool.QueuePool` so
that ``pool.size()`` is observable on every supported dialect — SQLite
in-memory would otherwise default to ``SingletonThreadPool`` whose
``size`` attribute is an integer (not a method), masking the
``pool_size`` configuration from the runtime probe in AC-6.5.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool


# AC-6.5 — pool configuration constants. ``TASKQ_DB_POOL_SIZE`` defaults
# to 5 per SPEC §5.1; ``pool_pre_ping`` is a fixed True (not env
# configurable) because stale-connection recycling is an unconditional
# correctness property, not a tunable.
_DEFAULT_POOL_SIZE = 5
_ENV_POOL_SIZE = "TASKQ_DB_POOL_SIZE"
_ENV_DATABASE_URL = "TASKQ_DATABASE_URL"
_DEFAULT_DATABASE_URL = "sqlite:///./taskq.db"


def _resolve_pool_size(explicit: int | None = None) -> int:
    """Return the configured ``pool_size`` for the engine.

    Precedence: explicit argument (when provided) → ``TASKQ_DB_POOL_SIZE``
    environment variable → :data:`_DEFAULT_POOL_SIZE`. Non-integer env
    values fall back to the default rather than raise — the probe is
    about connection availability, not strict type checking.
    """
    if explicit is not None:
        return int(explicit)
    raw = os.environ.get(_ENV_POOL_SIZE)
    if raw is None or raw == "":
        return _DEFAULT_POOL_SIZE
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_POOL_SIZE


def create_engine(url: str, **kwargs: object) -> Engine:
    """Build a SQLAlchemy engine wired for FR-06 pooling. [FR-06]

    Citations: SPEC.md §3 FR-06 (AC-6.5); SPEC.md §5.1.

    Args:
        url: SQLAlchemy database URL (e.g. ``sqlite:///./taskq.db``).
        **kwargs: forwarded to :func:`sqlalchemy.create_engine`. When
            ``pool_size`` is omitted, it defaults to
            :func:`_resolve_pool_size`; ``pool_pre_ping`` defaults to
            ``True`` so stale connections are recycled before they are
            handed to a request.

    Returns:
        Engine: a configured SQLAlchemy engine with
        ``pool_size`` and ``pool_pre_ping=True`` applied.
    """
    kwargs.setdefault("pool_size", _resolve_pool_size())
    kwargs.setdefault("pool_pre_ping", True)
    # Force QueuePool so ``engine.pool.size()`` is a callable regardless
    # of dialect. SQLite ``:memory:`` defaults to ``SingletonThreadPool``
    # whose ``size`` is an int attribute — that hides the configured
    # pool size from runtime probes (AC-6.5).
    kwargs.setdefault("poolclass", QueuePool)
    return sqlalchemy.create_engine(url, **kwargs)


def engine_from_env() -> Engine:
    """Build an engine from environment variables. [FR-06]

    Citations: SPEC.md §3 FR-06 (AC-6.5); SPEC.md §5.1.

    Reads ``TASKQ_DATABASE_URL`` (default ``sqlite:///./taskq.db``) and
    ``TASKQ_DB_POOL_SIZE`` (default 5) and returns a fully-configured
    engine suitable for the /readyz probe (FR-09) and migrations.

    Returns:
        Engine: a SQLAlchemy engine with ``pool_size`` and
        ``pool_pre_ping=True`` applied.
    """
    url = os.environ.get(_ENV_DATABASE_URL, _DEFAULT_DATABASE_URL)
    pool_size = _resolve_pool_size()
    return create_engine(url, pool_size=pool_size, pool_pre_ping=True)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Yield a ``Session`` scoped to a single API request. [FR-06]

    Citations: SPEC.md §3 FR-06 (AC-6.2); NFR-03.

    The context manager commits the transaction on clean exit and rolls
    back when the with-block raises, re-raising the original exception
    so callers observe both the rollback and the failure surface. One
    ``Session`` is opened per with-block and closed on exit, satisfying
    AC-6.2's "one Session per API request" mandate.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "create_engine",
    "engine_from_env",
    "session_scope",
]