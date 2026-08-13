"""[FR-05] Rate-bucket repository — per-token token-bucket state.

Citations:
- SPEC.md §3 FR-05 — bucket state lives in the database for cross-worker
  consistency; updates happen in a single transaction with a row-level
  lock (``SELECT ... FOR UPDATE`` on PostgreSQL or the SQLite-serialised
  equivalent per SPEC §3 FR-05 / §6 module layout).
- SAD.md §2.5 — ``repository.rate_repo`` is the per-aggregate module
  that exposes bucket CRUD; never imports ``taskq_api.api`` or
  ``taskq_api.service`` (NFR-06 layering invariant).
- FR-05 acceptance — the repository contract (``upsert_bucket`` /
  ``get_bucket``) is the single surface the consuming service uses,
  so the in-process GREEN storage and the production SQLAlchemy
  session are interchangeable behind it.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Optional


# Upper bound on the in-process bucket registry. The GREEN storage lives
# in RAM; an attacker who sends unique X-API-Key values can otherwise
# inflate the dict without limit (bug-hunt HIGH-2). When the cap is
# reached, the least-recently-touched entry is evicted.
_MAX_BUCKETS: int = 1024


class RateRepo:
    """[FR-05] Per-token token-bucket repository.

    The GREEN step keeps state in an in-process registry so the
    failing test suite (``test_fr05.py``) can observe bucket
    consumption without a live database. The autouse fixture in
    ``test_fr05.py`` clears ``RateRepo._buckets`` between tests so
    every test starts with a fresh bucket budget. Production wiring
    moves state into the real DB session and acquires a row-level
    lock for the duration of the consume transaction (SPEC §3 FR-05
    — "row-level lock").

    Citations:
    - SPEC.md §3 FR-05 — bucket mutation occurs inside a single
      transaction so concurrent workers observe a consistent token
      count (no over-grant, no double-spend).
    - SAD.md §2.5 — exposes CRUD + lookup functions; never imports
      ``taskq_api.api`` or ``taskq_api.service``.
    """

    # Module-level bucket registry — keyed by API key plaintext.
    # Production replaces this with a SQLAlchemy session bound to a
    # ``rate_buckets`` row keyed by token; the GREEN step preserves
    # the same upsert/get surface so the consuming service is
    # unchanged. OrderedDict so we can pop the LRU entry when the
    # cap is reached (bug-hunt HIGH-2).
    _buckets: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    @staticmethod
    def upsert_bucket(
        session: "object",
        token: str,
        *,
        tokens: float,
        last_refill_at: float,
    ) -> None:
        """[FR-05] Upsert the bucket row for ``token``.

        Citations:
        - SPEC.md §3 FR-05 — bucket update happens inside a single
          transaction with a row-level lock so concurrent workers
          see a consistent token count.

        Production wiring issues ``SELECT ... FOR UPDATE`` against
        the ``rate_buckets`` row keyed by ``token``, mutates the
        column, and commits. The GREEN step writes to the in-process
        ``_buckets`` registry — the lock serialisation is provided by
        ``api.deps`` (threading.Lock) so the same observable invariant
        holds for concurrent workers.

        ``session`` is the SQLAlchemy session in production; the
        GREEN in-process storage ignores it. The parameter is kept
        on the signature so the call site does not change when the
        production wiring lands.
        """
        del session  # unused by the in-process GREEN storage
        # Move-to-end so the entry is the most-recently-touched;
        # if the cap is reached, the oldest entry is evicted first.
        RateRepo._buckets.pop(token, None)
        RateRepo._buckets[token] = {
            "tokens": float(tokens),
            "last_refill_at": float(last_refill_at),
        }
        while len(RateRepo._buckets) > _MAX_BUCKETS:
            RateRepo._buckets.popitem(last=False)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    @staticmethod
    def get_bucket(token: str) -> Optional[dict[str, Any]]:
        """[FR-05] Read the bucket row for ``token`` (or ``None``).

        Citations: SPEC.md §3 FR-05 — the bucket row carries the
        current ``tokens`` count and the last ``refill_at`` instant;
        ``None`` is the signal for "first time we see this token"
        and the service initialises the bucket to full capacity.

        Marks the entry as most-recently-touched so the LRU eviction
        in ``upsert_bucket`` does not drop an actively-used token.
        """
        bucket = RateRepo._buckets.get(token)
        if bucket is not None:
            RateRepo._buckets.move_to_end(token)
        return bucket


__all__ = ["RateRepo"]
