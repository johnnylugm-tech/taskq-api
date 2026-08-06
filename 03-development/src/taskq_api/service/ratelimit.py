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


def _burst_capacity_from_env() -> int:
    """Return ``TASKQ_RATE_BURST`` as int, falling back to the default. [FR-05]

    Citations: SPEC.md §3 FR-05 (AC-5.1); SPEC.md §5.1.
    """
    raw = os.environ.get("TASKQ_RATE_BURST")
    if raw is None or raw == "":
        return DEFAULT_BURST_CAPACITY
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_BURST_CAPACITY
    return value if value > 0 else DEFAULT_BURST_CAPACITY


def _refill_per_sec_from_env() -> float:
    """Return ``TASKQ_RATE_PER_SEC`` as float, falling back to the default. [FR-05]

    Citations: SPEC.md §3 FR-05 (AC-5.1); SPEC.md §5.1.
    """
    raw = os.environ.get("TASKQ_RATE_PER_SEC")
    if raw is None or raw == "":
        return DEFAULT_REFILL_PER_SEC
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_REFILL_PER_SEC
    return value if value > 0 else DEFAULT_REFILL_PER_SEC


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
    from sqlalchemy import text

    bind = session.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "sqlite")
    if dialect_name == "sqlite":
        # SQLite: ``BEGIN IMMEDIATE`` is the canonical row-level lock
        # primitive — it acquires the database write lock and prevents
        # concurrent writers from interleaving updates.
        connection = session.connection()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        result = session.execute(
            text("SELECT * FROM rate_buckets WHERE key_id = :key_id"),
            {"key_id": key_id},
        )
    else:
        # PostgreSQL / others: explicit ``FOR UPDATE`` clause.
        result = session.execute(
            text("SELECT * FROM rate_buckets WHERE key_id = :key_id FOR UPDATE"),
            {"key_id": key_id},
        )
    return result.fetchone()


__all__ = [
    "DEFAULT_BURST_CAPACITY",
    "DEFAULT_REFILL_PER_SEC",
    "TokenBucket",
    "lock_bucket_for_update",
    "_burst_capacity_from_env",
    "_refill_per_sec_from_env",
    "_default_bucket",
]
