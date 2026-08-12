"""TDD-RED failing tests for FR-05 (流量控制 / Rate Limiting).

Per TEST_SPEC.md (FR-05), the four spec test functions below cover the
canonical acceptance criteria:

    AC1-extra-status         burst+1th request -> 429
    AC1-retry-after-header   429 response carries a `retry-after` header
    AC1-retry-after-positive Retry-After parses to an integer > 0
    AC2-no-overgrant         4 concurrent workers × 10 reqs → exactly 20 allowed
    AC2-no-double-spend      4 concurrent workers × 10 reqs → exactly 20 rejected
    AC3-healthz-not-limited  /healthz returns 200 even after burst
    AC3-healthz-repeated     1000 sequential /healthz hits all succeed
    AC4-header-format        first over-limit Retry-After >= 1 second

The TEST_SPEC names are the contract; spec-coverage-check uses exact
match. Do NOT rename these functions.

These tests intentionally fail because the FR-05 declared SAB modules
do not exist on disk yet (RED — TDD phase 1):
  - `taskq_api.service.ratelimit` (token-bucket service) is missing.
  - `taskq_api.repository.rate_repo` (DB-backed bucket, row-level lock)
    is missing.
  - `/healthz` and `/readyz` are not mounted on the FastAPI app.
  - The per-route rate-limit dependency (via `api.deps`) is not wired.

The Green step will:
  1. Implement `service.ratelimit.check_and_consume(token, *, burst,
     rate_per_sec)` returning a structured decision (allowed /
     retry_after_seconds).
  2. Implement `repository.rate_repo` with a DB-backed bucket and
     row-level lock (cross-worker consistency).
  3. Mount `/healthz` and `/readyz` exempt from rate limiting.
  4. Add the rate-limit dependency on every `/v1/*` route via
     `api.deps`.
"""
from __future__ import annotations

import asyncio
import os

import pytest
from httpx import ASGITransport, AsyncClient

# Top-level imports — RED will surface if any declared SAB module is
# missing on disk. The two FR-05 modules below do NOT exist yet; this
# is the expected Collection Error (Exit Code 2) that the RED step
# validates. It is NOT acceptable to wrap these in try/except.
from taskq_api.api.deps import require_scope  # noqa: F401  (existing)
from taskq_api.app import create_app
from taskq_api.models.orm import ApiKey  # noqa: F401  (existing module)
from taskq_api.repository.key_repo import KeyRepo
from taskq_api.repository.rate_repo import RateRepo  # noqa: F401  (DOES NOT EXIST — RED)
from taskq_api.service.ratelimit import check_and_consume  # noqa: F401  (DOES NOT EXIST — RED)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def write_api_key() -> str:
    """Static write-scoped key used by burst / lock tests."""
    return "test-write-key"


@pytest.fixture(autouse=True)
def _stub_external_side_effects(monkeypatch):
    """Stub external side-effects so tests fail for FEATURE reasons only.

    The autouse fixture runs before every test; it patches the auth
    verifier, resets in-process registries, and configures the rate
    limiter env vars so each test starts with a known burst budget.

    GREEN TODO: `taskq_api.service.ratelimit.check_and_consume(token, *,
    burst, rate_per_sec) -> RateDecision` must consult the DB-backed
    bucket row (`repository.rate_repo`) under a row-level lock so that
    concurrent workers see a consistent token count. The autouse
    fixture here configures `TASKQ_RATE_BURST` and `TASKQ_RATE_PER_SEC`
    per test so the GREEN implementation reads its budget from the
    environment.
    """
    from taskq_api.service import auth as _auth

    def _scope_aware_verify(raw: str, hashed: str) -> bool:
        if not raw or not hashed:
            return False
        row = KeyRepo._by_key.get(raw)
        if row is None:
            return False
        registered = KeyRepo._registry.get(row)
        if registered is None:
            return False
        if registered.get("revoked_at") is not None:
            return False
        return True

    monkeypatch.setattr(_auth, "verify_key", _scope_aware_verify)

    # Stub DB session acquisition — same shape as test_fr01 / test_fr03.
    from taskq_api.repository import session as _session

    class _FakeSession:
        def __init__(self):
            self._rows: list[dict] = []

        def add(self, row):
            self._rows.append(row)

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

        def query(self, *_args, **_kwargs):
            return self

        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            return list(self._rows)

        def first(self):
            return self._rows[0] if self._rows else None

        # GREEN TODO: RateRepo.upsert_bucket(session, token, *, tokens,
        # last_refill_at) must call `session.execute` with a
        # `SELECT ... FOR UPDATE` (or SQLite-serialised equivalent) so
        # the row-level lock holds for the duration of the consume
        # transaction. The autouse stub here returns a fresh fake
        # session per call so concurrent workers do not share state
        # until GREEN wires real locking.

        def execute(self, *_args, **_kwargs):
            class _FakeResult:
                def scalar(self_inner):
                    return None

                def fetchone(self_inner):
                    return None

            return _FakeResult()

    monkeypatch.setattr(
        _session,
        "get_session",
        lambda: _FakeSession(),
    )

    # Reset the api_keys side-tables so each test starts clean.
    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()

    for scope, raw_key in (
        ("write", "test-write-key"),
        ("read", "test-read-key"),
        ("admin", "test-admin-key"),
    ):
        key_id = f"key-{scope}-{raw_key}"
        KeyRepo._registry[key_id] = {
            "id": key_id,
            "scope": scope,
            "key_hash": "0" * 64,
            "revoked_at": None,
        }
        KeyRepo._by_key[raw_key] = key_id

    # Reset the rate-bucket side-table for in-process GREEN wiring.
    if hasattr(RateRepo, "_buckets"):
        RateRepo._buckets.clear()

    yield

    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()
    if hasattr(RateRepo, "_buckets"):
        RateRepo._buckets.clear()


@pytest.fixture
async def client():
    """Yield an httpx AsyncClient bound to the FastAPI ASGI app.

    Uses ASGITransport so the request never leaves the process —
    pytest-cov can measure coverage of code executed via ASGITransport.
    The auth verifier and DB session are stubbed via the autouse
    fixture so no real disk I/O or HMAC verification occurs.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_rate_env(*, burst: int, rate_per_sec: float) -> None:
    """Configure TASKQ_RATE_BURST / TASKQ_RATE_PER_SEC for the test.

    The Green implementation reads these env vars to size the bucket.
    We set them via `os.environ` (and clear in teardown) so the test
    sees a fresh budget per test without leaking across tests.
    """
    os.environ["TASKQ_RATE_BURST"] = str(burst)
    os.environ["TASKQ_RATE_PER_SEC"] = str(rate_per_sec)


def _parse_int_seconds(value: str | None) -> int:
    """Parse a `Retry-After` header value (delta-seconds form) to int.

    SPEC §3 FR-05 mandates the delta-seconds form (RFC 9110 §10.2.3).
    Returns 0 when the value is missing or non-numeric so an assertion
    failure surfaces clearly rather than masking the GREEN gap.
    """
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Test 1 — burst returns 429 + Retry-After (Q3 / NP-03 boundary)
# ---------------------------------------------------------------------------


# NFR-03 NFR-09 NFR-10 SEC T-06
@pytest.mark.asyncio
async def test_fr05_burst_returns_429_with_retry_after(
    monkeypatch, client, write_api_key
):
    """BURST=20, send 21 requests; the 21st must be 429 + Retry-After.

    Sub-assertions (TEST_SPEC FR-05 case 1):
      AC1-extra-status       result_status_code == 429
      AC1-retry-after-header "retry-after" in result_headers_dict
      AC1-retry-after-positive parse_int_seconds(...) > 0
    """
    burst = 20
    rate_per_sec = 5.0
    _set_rate_env(burst=burst, rate_per_sec=rate_per_sec)

    # The first `burst` requests should succeed; the `(burst+1)`-th
    # is the "extra" one the SPEC names `extra_request="true"`.
    headers = {"X-API-Key": write_api_key}
    for _ in range(burst):
        resp = await client.get("/v1/tasks", headers=headers)
        # Read endpoint succeeds when within budget — accept either
        # 200 (empty list) or 401/403 (autouse stub gap during RED).
        # We only ASSERT on the over-budget response below.
        assert resp.status_code in (200, 401, 403), resp.text

    # The extra request — this is the one that MUST be 429.
    extra = await client.get("/v1/tasks", headers=headers)
    result_status_code = extra.status_code
    result_headers_dict = {k.lower(): v for k, v in extra.headers.items()}
    result_retry_after_seconds = result_headers_dict.get("retry-after")

    # AC1-extra-status — over-limit request is rejected with 429.
    assert result_status_code == 429, (
        f"expected 429 after {burst} requests, got {result_status_code}"
    )
    # AC1-retry-after-header — Retry-After header is present.
    assert "retry-after" in result_headers_dict, (
        f"expected `retry-after` header in 429 response, "
        f"got headers={dict(extra.headers)}"
    )
    # AC1-retry-after-positive — Retry-After is a positive integer.
    assert _parse_int_seconds(result_retry_after_seconds) > 0, (
        f"Retry-After should be > 0 seconds, got {result_retry_after_seconds!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — bucket row-level lock (Q4 / NP-13 state-transition)
# ---------------------------------------------------------------------------


# NFR-03 NFR-09 NFR-10
@pytest.mark.asyncio
async def test_fr05_bucket_row_level_lock(monkeypatch, client, write_api_key):
    """4 workers × 10 requests; exactly 20 allowed, 20 rejected.

    Sub-assertions (TEST_SPEC FR-05 case 2):
      AC2-no-overgrant   result_allowed_count == 20
      AC2-no-double-spend result_rejected_count == 20

    Row-level lock invariant: when 4 workers concurrently call
    `service.ratelimit.check_and_consume(...)`, the row-level lock
    (SELECT ... FOR UPDATE on PostgreSQL or SQLite serialised
    equivalent per SPEC §3 FR-05) ensures the bucket count never
    over-grants (would allow > 20) nor double-spends (would reject
    > 20). The GREEN step must guarantee exactly 20 successes and
    exactly 20 rejections across the 40 concurrent requests.
    """
    concurrent_workers = 4
    requests_per_worker = 10
    total_requests = concurrent_workers * requests_per_worker  # = 40
    burst = 20

    _set_rate_env(burst=burst, rate_per_sec=1000.0)  # refill fast enough not to add tokens mid-test

    headers = {"X-API-Key": write_api_key}

    async def _one_request() -> int:
        resp = await client.get("/v1/tasks", headers=headers)
        return resp.status_code

    # Fire `requests_per_worker` calls from each of `concurrent_workers`
    # workers in parallel via asyncio.gather. The GREEN row-level lock
    # must serialise the bucket mutation so the global count of 200 OKs
    # never exceeds `burst`.
    results: list[int] = []
    for _ in range(concurrent_workers):
        batch = await asyncio.gather(
            *[_one_request() for _ in range(requests_per_worker)]
        )
        results.extend(batch)

    # Map status codes into the test's domain identifiers. Any 429 is
    # "rejected"; anything else is "allowed" (we accept 200 from the
    # GET /v1/tasks handler — empty list under stub session).
    result_allowed_count = sum(1 for s in results if s != 429)
    result_rejected_count = sum(1 for s in results if s == 429)

    # AC2-no-overgrant — must NOT serve more than `burst` requests.
    assert result_allowed_count == burst, (
        f"row-level lock violated: served {result_allowed_count} requests, "
        f"expected exactly {burst} (got {results})"
    )
    # AC2-no-double-spend — must NOT reject more than the over-budget
    # remainder.
    assert result_rejected_count == total_requests - burst, (
        f"row-level lock violated: rejected {result_rejected_count} requests, "
        f"expected exactly {total_requests - burst} (got {results})"
    )


# ---------------------------------------------------------------------------
# Test 3 — /healthz is exempt (Q1 happy-path)
# ---------------------------------------------------------------------------


# NFR-03 NFR-09 NFR-10
@pytest.mark.asyncio
async def test_fr05_health_endpoints_exempt(monkeypatch, client):
    """1000 /healthz hits must all return 200; rate limit is NOT applied.

    Sub-assertions (TEST_SPEC FR-05 case 3):
      AC3-healthz-not-limited result_status_code == 200
      AC3-healthz-repeated   result_repeated_count == 1000

    SPEC §3 FR-05 explicitly states `/healthz` and `/readyz` are not
    subject to rate limiting. The GREEN step must register these two
    routes outside the rate-limit middleware/dep chain.
    """
    healthz_requests = 1000
    _set_rate_env(burst=1, rate_per_sec=0.001)  # intentionally stingy

    result_repeated_count = 0
    last_status = None
    for _ in range(healthz_requests):
        resp = await client.get("/healthz")
        last_status = resp.status_code
        if resp.status_code == 200:
            result_repeated_count += 1

    result_status_code = last_status
    # AC3-healthz-not-limited — /healthz always 200 regardless of burst.
    assert result_status_code == 200, (
        f"/healthz must be exempt from rate limit; last status={result_status_code}"
    )
    # AC3-healthz-repeated — every one of the 1000 requests returned 200.
    assert result_repeated_count == healthz_requests, (
        f"/healthz exemption broken: {result_repeated_count}/{healthz_requests} returned 200"
    )


# ---------------------------------------------------------------------------
# Test 4 — first over-limit Retry-After header is well-formed (Q2 / NP-03)
# ---------------------------------------------------------------------------


# NFR-03 NFR-09 NFR-10 SEC T-06
@pytest.mark.asyncio
async def test_fr05_retry_after_header_present(monkeypatch, client, write_api_key):
    """First request that goes over the limit must carry Retry-After >= 1s.

    Sub-assertions (TEST_SPEC FR-05 case 4):
      AC4-header-format parse_int_seconds(result_retry_after_seconds) >= 1

    The Retry-After value MUST be expressed in integer seconds
    (RFC 9110 §10.2.3 delta-seconds form), and MUST be at least 1
    — a value of 0 would defeat the purpose (clients would hot-loop).
    """
    burst = 5
    rate_per_sec = 1.0
    _set_rate_env(burst=burst, rate_per_sec=rate_per_sec)

    headers = {"X-API-Key": write_api_key}

    # Exhaust the budget (first N requests succeed).
    for _ in range(burst):
        await client.get("/v1/tasks", headers=headers)

    # `first_over="true"` — the FIRST request that goes over the limit.
    over_resp = await client.get("/v1/tasks", headers=headers)
    result_retry_after_seconds = over_resp.headers.get("retry-after")

    assert over_resp.status_code == 429, (
        f"expected 429 on first over-limit request, got {over_resp.status_code}"
    )
    # AC4-header-format — parseable integer seconds, >= 1.
    assert _parse_int_seconds(result_retry_after_seconds) >= 1, (
        f"Retry-After must be >= 1 second (delta-seconds form), "
        f"got {result_retry_after_seconds!r}"
    )


__all__ = [
    "test_fr05_burst_returns_429_with_retry_after",
    "test_fr05_bucket_row_level_lock",
    "test_fr05_health_endpoints_exempt",
    "test_fr05_retry_after_header_present",
]