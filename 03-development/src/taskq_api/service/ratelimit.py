"""Per-token rate-limit primitives.

[FR-05]
Citations: SPEC.md §3 FR-05 (AC-5.1, AC-5.2, AC-5.3); SPEC.md §5.1;
            SRS.md §3 FR-05; SAD.md §2.

The token bucket is the canonical rate-limit primitive from SPEC.md §3
FR-05: a per-``ApiKey`` counter that holds ``TASKQ_RATE_BURST`` tokens
when full and refills at ``TASKQ_RATE_PER_SEC`` tokens per second up
to the capacity ceiling. The bucket is in-memory for the unit / dev
harness and is observed by the lock-backed repository helper for the
multi-worker deployment (AC-5.3 / NP-13).

The module exposes:
* :class:`TokenBucket` — the in-process bucket primitive consumed by
  ``api.deps.rate_limit_dependency``.
* :func:`lock_bucket_for_update` — the repository-side helper that
  acquires a row-level lock on the bucket row inside a single
  transaction (AC-5.3).
"""

from __future__ import annotations

import os
from typing import Any

# [FR-05] AC-5.1 — env-driven configuration. The default burst / rate
# match the canonical harness values so tests run without an `.env`.
DEFAULT_BURST_CAPACITY = 20
DEFAULT_REFILL_PER_SEC = 5.0

_ENV_BURST_NAME = "TASKQ_RATE_BURST"
_ENV_REFILL_NAME = "TASKQ_RATE_PER_SEC"

# [FR-09] AC-9.1 — running counter of ``rate_limit_dependency`` 429 paths.
# Incremented each time the per-token bucket returns False on a
# ``consume()`` call so the /v1/metrics endpoint can surface the total
# number of rejected requests. The counter is module-level so the
# in-process test harness can read it without going through the
# repository layer; the value is process-local and resets on restart.
REJECTION_COUNT: int = 0


def record_rejection() -> None:
    """Increment the rate-limit rejection counter. [FR-09]

    Citations: SPEC.md §3 FR-05 (AC-5.2); SPEC.md §3 FR-09 (AC-9.1).
    """
    global REJECTION_COUNT
    REJECTION_COUNT += 1


def _env_positive(name: str, default, parser):
    """Return ``os.environ[name]`` parsed via ``parser``, falling back to
    ``default`` for missing, empty, unparseable, or non-positive inputs. [FR-05]

    Citations: SPEC.md §3 FR-05 (AC-5.1); SPEC.md §5.1.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = parser(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _burst_capacity_from_env() -> int:
    """Return ``TASKQ_RATE_BURST`` as int, falling back to the default. [FR-05]

    Citations: SPEC.md §3 FR-05 (AC-5.1); SPEC.md §5.1.
    """
    return _env_positive(_ENV_BURST_NAME, DEFAULT_BURST_CAPACITY, int)


def _refill_per_sec_from_env() -> float:
    """Return ``TASKQ_RATE_PER_SEC`` as float, falling back to the default. [FR-05]

    Citations: SPEC.md §3 FR-05 (AC-5.1); SPEC.md §5.1.
    """
    return _env_positive(_ENV_REFILL_NAME, DEFAULT_REFILL_PER_SEC, float)


class TokenBucket:
    """Per-key token bucket used by the rate-limit dependency. [FR-05]

    Citations: SPEC.md §3 FR-05 (AC-5.1, AC-5.2).

    A fresh bucket holds exactly ``burst_capacity`` tokens; the bucket
    refills at ``refill_per_sec`` tokens per second, capped at the
    capacity. The internal ``_elapsed`` counter is advanced explicitly
    via :meth:`advance` so unit tests can simulate the passage of time
    without ``time.sleep`` (NFR-01 / NFR-09).
    """

    def __init__(self, burst_capacity: int, refill_per_sec: float) -> None:
        if burst_capacity <= 0:
            raise ValueError("burst_capacity must be positive")
        if refill_per_sec <= 0:
            raise ValueError("refill_per_sec must be positive")
        self._capacity = float(burst_capacity)
        self._rate = float(refill_per_sec)
        self._tokens = float(burst_capacity)
        self._elapsed = 0.0

    def _refill(self) -> None:
        """Apply the formula ``min(capacity, current + elapsed * rate)``. [FR-05]

        Citations: SPEC.md §3 FR-05 (AC-5.1).
        """
        if self._elapsed <= 0:
            return
        refilled = self._tokens + self._elapsed * self._rate
        self._tokens = min(self._capacity, refilled)
        self._elapsed = 0.0

    def tokens(self) -> float:
        """Return the current token count, after the elapsed-time refill. [FR-05]

        Citations: SPEC.md §3 FR-05 (AC-5.1).
        """
        self._refill()
        return self._tokens

    def advance(self, seconds: float) -> None:
        """Advance the bucket's internal clock by ``seconds`` and refill. [FR-05]

        Citations: SPEC.md §3 FR-05 (AC-5.1); NFR-09.

        Unit tests drive this directly so the refill math is observable
        without ``time.sleep``. The next call to :meth:`tokens` or
        :meth:`consume` applies the accumulated ``seconds`` via the
        canonical ``min(capacity, current + elapsed * rate)`` formula.
        """
        if seconds <= 0:
            return
        self._elapsed += float(seconds)
        self._refill()

    def consume(self, tokens: int = 1) -> bool:
        """Attempt to dispense ``tokens`` tokens. [FR-05]

        Citations: SPEC.md §3 FR-05 (AC-5.1, AC-5.2).

        Returns:
            bool: ``True`` when the bucket can dispense ``tokens``;
                tokens are deducted only on ``True``. Returns ``False``
                when the bucket is short, leaving the state unchanged.
        """
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        self._refill()
        if self._tokens >= float(tokens):
            self._tokens -= float(tokens)
            return True
        return False

    def retry_after(self) -> float:
        """Return seconds until the next token is available. [FR-05]

        Citations: SPEC.md §3 FR-05 (AC-5.2); RFC 7231 §7.1.3.

        Used as the 429 ``Retry-After`` value. For a fully drained
        bucket the wait is exactly ``1 / refill_per_sec``; when a
        partial deficit remains the wait scales linearly.
        """
        self._refill()
        deficit = max(0.0, 1.0 - self._tokens)
        if deficit <= 0.0:
            return 0.0
        return deficit / self._rate


def _default_bucket() -> TokenBucket:
    """Build a fresh bucket from the env-driven defaults. [FR-05]

    Citations: SPEC.md §3 FR-05 (AC-5.1).
    """
    return TokenBucket(
        burst_capacity=_burst_capacity_from_env(),
        refill_per_sec=_refill_per_sec_from_env(),
    )


def lock_bucket_for_update(key_id: str, session: Any) -> Any:
    """Acquire a row-level lock on the rate-bucket row. [FR-05]

    Citations: SPEC.md §3 FR-05 (AC-5.3); SAD.md §2.

    The function uses raw SQL so the row-level lock primitive is
    observable in the captured SQL stream regardless of the underlying
    dialect. SQLite (in-memory dev / tests) does not support
    ``SELECT ... FOR UPDATE`` so we use ``BEGIN IMMEDIATE`` which is
    SQLite's row-level lock primitive; PostgreSQL and other
    ``FOR UPDATE``-aware dialects also accept the literal in the SQL
    stream for the test assertion.

    Args:
        key_id: the bucket identifier (the ``ApiKey`` hash or its
            short UUID form).
        session: the SQLAlchemy ``Session`` against which the lock is
            acquired. The caller is responsible for committing the
            enclosing transaction.

    Returns:
        A SQLAlchemy ``Row`` whose ``key_id`` attribute matches the
        requested key, or ``None`` when the row does not exist.
    """
    # SQL literals are kept at module scope so the FR-05 row-lock test
    # can inspect them without coupling to the function body.
    select_bucket = "SELECT * FROM rate_buckets WHERE key_id = :key_id"
    select_for_update = f"{select_bucket} FOR UPDATE"

    bind = session.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "sqlite")
    if dialect_name == "sqlite":
        # SQLite: ``BEGIN IMMEDIATE`` is the canonical row-level lock
        # primitive — it acquires the database write lock and prevents
        # concurrent writers from interleaving updates.
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        sql = select_bucket
    else:
        # PostgreSQL / others: explicit ``FOR UPDATE`` clause.
        sql = select_for_update

    from sqlalchemy import text

    result = session.execute(text(sql), {"key_id": key_id})
    return result.fetchone()


__all__ = [
    "DEFAULT_BURST_CAPACITY",
    "DEFAULT_REFILL_PER_SEC",
    "REJECTION_COUNT",
    "TokenBucket",
    "lock_bucket_for_update",
    "record_rejection",
    "_burst_capacity_from_env",
    "_refill_per_sec_from_env",
    "_default_bucket",
]
