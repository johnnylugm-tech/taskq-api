"""Bug-hunt repro: RateRepo._buckets has no max-size cap. A flood of
unique X-API-Key tokens (each consumed by the rate-limit gate BEFORE
verify_key) can grow the in-process dict without bound.

RED proof: drive upsert_bucket with more than the bound and assert the
dict stays bounded.
"""
from __future__ import annotations

from taskq_api.repository.rate_repo import RateRepo


def test_rate_repo_bounds_bucket_dict_growth():
    """Filling past the cap must keep the dict bounded.

    Bug: RateRepo._buckets had no eviction policy; each unique token
    consumed a slot forever.
    """
    RateRepo._buckets.clear()
    try:
        # The fix introduces _MAX_BUCKETS. Cap MUST be reachable from tests.
        from taskq_api.repository import rate_repo as _rr
        cap = getattr(_rr, "_MAX_BUCKETS", None)
        assert cap is not None and cap > 0, (
            "rate_repo._MAX_BUCKETS missing — bucket dict has no bound"
        )

        # Insert far more entries than the cap.
        for i in range(cap * 3):
            RateRepo.upsert_bucket(None, f"token-{i}", tokens=1.0, last_refill_at=0.0)

        assert len(RateRepo._buckets) <= cap, (
            f"RateRepo._buckets exceeded cap: {len(RateRepo._buckets)} > {cap}"
        )
    finally:
        RateRepo._buckets.clear()
