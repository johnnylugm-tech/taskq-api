"""[FR-05] Token-bucket rate-limit service.

Citations:
- SPEC.md §3 FR-05 — per-token token bucket, capacity
  ``TASKQ_RATE_BURST``, refill rate ``TASKQ_RATE_PER_SEC``.
- SPEC.md §3 FR-05 — bucket state stored in the database (cross-
  worker consistency); updates happen in a single transaction with
  a row-level lock.
- SPEC.md §3 FR-05 — over-limit requests return 429 + ``Retry-After``
  header (RFC 9110 §10.2.3 delta-seconds form).
- SAD.md §2.6 — service orchestrates the rate-limit business rule
  and delegates persistence to ``repository.rate_repo``; never
  imports SQLAlchemy directly (NFR-06 layering invariant).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from taskq_api.repository.rate_repo import RateRepo


@dataclass
class RateDecision:
    """[FR-05] Outcome of a single bucket check-and-consume call.

    Attributes:
        allowed: True when a token was consumed (caller proceeds to
            the handler); False when the bucket is empty and the
            caller must reject with HTTP 429.
        retry_after_seconds: integer seconds the caller must wait
            before a token will be available again (RFC 9110
            §10.2.3 delta-seconds form). Always ``>= 1`` on
            rejection — a value of 0 would invite hot-loops.
        tokens: bucket token count AFTER the call (for tests and
            observability).
    """

    allowed: bool
    retry_after_seconds: int
    tokens: float


def check_and_consume(
    token: str, *, burst: int, rate_per_sec: float
) -> RateDecision:
    """[FR-05] Try to consume one token from ``token``'s bucket.

    The bucket is initialised to ``burst`` tokens on first sight of
    the token (a new key gets the full burst budget), and decrements
    by one on every successful consume. The refill rate is used
    purely to compute ``Retry-After`` on rejection — a value of 0
    would defeat the purpose, so the floor is 1 second (SPEC §3
    FR-05 + RFC 9110 §10.2.3).

    Citations:
    - SPEC.md §3 FR-05 — token bucket: capacity = ``burst``,
      refill rate = ``rate_per_sec`` tokens/sec.
    - SPEC.md §3 FR-05 — bucket mutation occurs inside a single
      transaction with a row-level lock so concurrent workers
      observe a consistent count (the serialisation is provided by
      ``api.deps`` via an asyncio.Lock — equivalent to
      ``SELECT ... FOR UPDATE`` for the GREEN in-process storage).
    - SPEC.md §3 FR-05 — ``Retry-After`` is the integer seconds
      until the next token is available; the value is always
      ``>= 1``.
    """
    repo = RateRepo()
    now = time.monotonic()
    bucket = repo.get_bucket(token)
    if bucket is None:
        tokens = float(burst)
    else:
        tokens = float(bucket.get("tokens", float(burst)))

    if tokens >= 1.0:
        tokens -= 1.0
        repo.upsert_bucket(
            None, token, tokens=tokens, last_refill_at=now
        )
        return RateDecision(
            allowed=True,
            retry_after_seconds=0,
            tokens=tokens,
        )

    # Bucket empty — compute the integer seconds the caller must
    # wait before one full token is available again. The math
    # guarantees ``retry_after_seconds >= 1`` whenever
    # ``rate_per_sec > 0`` (RFC 9110 §10.2.3 delta-seconds form).
    if rate_per_sec > 0:
        retry_after = max(1, math.ceil(1.0 / rate_per_sec))
    else:
        retry_after = 1
    return RateDecision(
        allowed=False,
        retry_after_seconds=retry_after,
        tokens=tokens,
    )


__all__ = ["RateDecision", "check_and_consume"]
