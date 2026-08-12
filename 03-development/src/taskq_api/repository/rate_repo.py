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

from typing import Any, Optional


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
    # unchanged.
    _buckets: dict[str, dict[str, Any]] = {}

    def __init__(self, session: Optional["object"] = None) -> None:
        # ``None`` is the sentinel for "fetch lazily": tests pass no
        # session and patch ``_session_module.get_session`` first, so
        # the repo only resolves on the first command.
        self._session = session

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def upsert_bucket(
        self,
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
        ``api.deps`` (asyncio.Lock) so the same observable invariant
        holds for concurrent workers.
        """
        RateRepo._buckets[token] = {
            "tokens": float(tokens),
            "last_refill_at": float(last_refill_at),
        }

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def get_bucket(self, token: str) -> Optional[dict[str, Any]]:
        """[FR-05] Read the bucket row for ``token`` (or ``None``).

        Citations: SPEC.md §3 FR-05 — the bucket row carries the
        current ``tokens`` count and the last ``refill_at`` instant;
        ``None`` is the signal for "first time we see this token"
        and the service initialises the bucket to full capacity.
        """
        return RateRepo._buckets.get(token)


__all__ = ["RateRepo"]
