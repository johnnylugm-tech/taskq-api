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


# ---------------------------------------------------------------------------
# Coverage tests — exercise every reachable line in deps.py / orm.py /
# ratelimit.py / rate_repo.py that the spec tests above do not visit.
#
# These are NOT new SPEC tests (the four above are the spec contract);
# they are unit tests that drive the production code paths that Gate 1's
# coverage dimension expects to be exercised. Each one targets one or
# more specific lines listed in the Gate 1 coverage report as Miss.
# ---------------------------------------------------------------------------


# NFR-09
def test_fr05_apikey_init_with_id_uses_provided_id():
    """[coverage] Exercise ``ApiKey.__init__`` lines 72-75.

    With a non-None ``id`` argument the constructor must use it instead
    of generating a UUID; the four attribute assignments on lines
    73-75 must all run. The default-uuid branch is exercised by the
    no-arg-id call in ``test_fr05_apikey_init_without_id_generates_uuid``
    below.
    """
    row = ApiKey(
        id="key-fixed-id-1",
        scope="write",
        key_hash="a" * 64,
        revoked_at=None,
    )
    assert row.id == "key-fixed-id-1"
    assert row.scope == "write"
    assert row.key_hash == "a" * 64
    assert row.revoked_at is None


# NFR-09
def test_fr05_apikey_init_without_id_generates_uuid():
    """[coverage] Exercise the ``id or str(uuid.uuid4())`` branch (line 72)."""
    import uuid as _uuid

    row = ApiKey(scope="read", key_hash="b" * 64)
    # uuid.uuid4() returns a string UUID; the constructor must produce one
    # when id is omitted.
    _uuid.UUID(row.id)  # raises if not a valid UUID


# NFR-09
def test_fr05_apikey_as_row_returns_field_dict():
    """[coverage] Exercise ``ApiKey.as_row`` line 89.

    as_row must materialise exactly the four FR-03 columns and the dict
    comprehension on line 89 must iterate all four. Revoked_at None is
    the normal state for an active key.
    """
    row = ApiKey(
        id="k1",
        scope="admin",
        key_hash="c" * 64,
        revoked_at=None,
    )
    as_dict = row.as_row()
    assert as_dict == {
        "id": "k1",
        "scope": "admin",
        "key_hash": "c" * 64,
        "revoked_at": None,
    }


# NFR-09
def test_fr05_apikey_repr_includes_all_fields():
    """[coverage] Exercise ``ApiKey.__repr__`` line 92.

    The f-string on line 92 must format all four attributes. A simple
    containment check is sufficient — we only need to confirm every
    field name appears in the rendered string.
    """
    row = ApiKey(
        id="rid",
        scope="write",
        key_hash="d" * 64,
        revoked_at=None,
    )
    rendered = repr(row)
    assert "rid" in rendered
    assert "write" in rendered
    assert ("d" * 64) in rendered
    assert "revoked_at" in rendered


# NFR-09
def test_fr05_taskresult_init_with_id_uses_provided_id():
    """[coverage] Exercise ``TaskResult.__init__`` lines 124-132.

    Provide every field including a non-None ``id`` so all nine
    attribute assignments on lines 125-132 run.
    """
    from taskq_api.models.orm import TaskResult

    row = TaskResult(
        id="res-fixed-1",
        task_id="task-1",
        run_id="run-1",
        exit_code=0,
        stdout_tail="out",
        stderr_tail="err",
        duration_ms=123,
        finished_at="2026-08-13T00:00:00Z",
        status="done",
    )
    assert row.id == "res-fixed-1"
    assert row.task_id == "task-1"
    assert row.run_id == "run-1"
    assert row.exit_code == 0
    assert row.stdout_tail == "out"
    assert row.stderr_tail == "err"
    assert row.duration_ms == 123
    assert row.finished_at == "2026-08-13T00:00:00Z"
    assert row.status == "done"


# NFR-09
def test_fr05_taskresult_init_without_id_generates_uuid():
    """[coverage] Exercise the ``id or str(uuid.uuid4())`` branch (line 124)."""
    import uuid

    from taskq_api.models.orm import TaskResult

    row = TaskResult(task_id="t", run_id="r")
    uuid.UUID(row.id)


# NFR-09
def test_fr05_taskresult_add_and_list_for_task_round_trip():
    """[coverage] Exercise ``TaskResult.add`` (line 145) and
    ``TaskResult.list_for_task`` (lines 154-158).

    add appends to the in-process registry; list_for_task filters by
    task_id and reverses so the most recent insertion comes first
    (Python dict preserves insertion order, 3.7+).
    """
    from taskq_api.models.orm import TaskResult

    # Snapshot and restore the module-level registry so the test does
    # not interfere with other suites that also share this attribute.
    snapshot = list(TaskResult._registry)
    TaskResult._registry.clear()
    try:
        first = TaskResult(task_id="tA", run_id="r1")
        TaskResult.add(first)
        # Insert a row for a different task — it MUST be filtered out
        # by list_for_task, exercising the comprehension on line 154.
        other = TaskResult(task_id="tB", run_id="rX")
        TaskResult.add(other)
        # Append a second row for tA — newest-first means this one
        # must appear before `first`, exercising the .reverse() on 157.
        second = TaskResult(task_id="tA", run_id="r2")
        TaskResult.add(second)

        rows = TaskResult.list_for_task("tA")
        assert len(rows) == 2
        # Newest-first by insertion order — second was appended last.
        assert rows[0].run_id == "r2"
        assert rows[1].run_id == "r1"
    finally:
        TaskResult._registry.clear()
        TaskResult._registry.extend(snapshot)


# NFR-09
def test_fr05_taskresult_from_dict_via_list_for_task():
    """[coverage] Exercise ``TaskResult._from_dict`` line 140.

    list_for_task rehydrates rows via _from_dict; if the comprehension
    on line 140 skipped any field, the returned object would have a
    missing attribute. Asserting attribute presence after a round-trip
    confirms the comprehension ran.
    """
    from taskq_api.models.orm import TaskResult

    snapshot = list(TaskResult._registry)
    TaskResult._registry.clear()
    try:
        # Manually append a dict-shaped row (bypassing TaskResult.add)
        # to mirror the on-disk row layout — list_for_task filters by
        # task_id and rehydrates via _from_dict which uses
        # cls(**{field: row[field] for field in _ROW_FIELDS}).
        TaskResult._registry.append(
            {
                "id": "row-d-1",
                "task_id": "tC",
                "run_id": "r9",
                "exit_code": 1,
                "stdout_tail": "o",
                "stderr_tail": "e",
                "duration_ms": 7,
                "finished_at": "2026-08-13T00:00:00Z",
                "status": "failed",
            }
        )
        rows = TaskResult.list_for_task("tC")
        assert len(rows) == 1
        round_tripped = rows[0]
        assert round_tripped.id == "row-d-1"
        assert round_tripped.task_id == "tC"
        assert round_tripped.run_id == "r9"
        assert round_tripped.exit_code == 1
        assert round_tripped.stdout_tail == "o"
        assert round_tripped.stderr_tail == "e"
        assert round_tripped.duration_ms == 7
        assert round_tripped.finished_at == "2026-08-13T00:00:00Z"
        assert round_tripped.status == "failed"
    finally:
        TaskResult._registry.clear()
        TaskResult._registry.extend(snapshot)


# NFR-09
def test_fr05_read_rate_config_returns_none_when_unset(monkeypatch):
    """[coverage] Exercise ``_read_rate_config`` early-return on line 74.

    When ``TASKQ_RATE_BURST`` is unset, ``_read_rate_config`` must
    return ``None`` so rate limiting is opt-in. This branch is the
    one the FR-01/FR-02/FR-03/FR-04 suites rely on.
    """
    monkeypatch.delenv("TASKQ_RATE_BURST", raising=False)

    from taskq_api.api import deps as _deps

    assert _deps._read_rate_config() is None


# NFR-09
def test_fr05_enforce_rate_limit_skips_when_config_none(monkeypatch):
    """[coverage] Exercise ``_enforce_rate_limit`` early-return on line 97.

    With no rate config, the function must return ``None`` (i.e. NOT
    raise HTTPException(429)) — this is the gate that lets prior
    suites run without throttling.
    """
    monkeypatch.delenv("TASKQ_RATE_BURST", raising=False)

    from taskq_api.api import deps as _deps

    # Must return None and NOT raise.
    assert _deps._enforce_rate_limit("any-token") is None


# NFR-09
@pytest.mark.asyncio
async def test_fr05_missing_api_key_header_returns_401(client):
    """[coverage] Exercise the missing-header branch on line 139 of deps.py.

    Without ``X-API-Key`` and with no rate env set (so the rate-limit
    branch in ``get_current_key`` is skipped), the dep must raise
    ``AuthProblem`` → 401 ``application/problem+json``.
    """
    # Ensure rate limit is NOT active so the auth check is reached.
    os.environ.pop("TASKQ_RATE_BURST", None)
    os.environ.pop("TASKQ_RATE_PER_SEC", None)

    resp = await client.get("/v1/tasks")
    assert resp.status_code == 401, resp.text
    # Problem+json content type per SPEC §3 FR-03.
    assert resp.headers["content-type"].startswith("application/problem+json")


# NFR-09
@pytest.mark.asyncio
async def test_fr05_invalid_api_key_returns_401(client):
    """[coverage] Exercise the invalid-key branch on line 144 of deps.py.

    Sending an unregistered key (with no rate env active) must reach
    ``_auth.verify_key`` which returns False, raising ``AuthProblem``
    → 401.
    """
    os.environ.pop("TASKQ_RATE_BURST", None)
    os.environ.pop("TASKQ_RATE_PER_SEC", None)

    resp = await client.get("/v1/tasks", headers={"X-API-Key": "not-a-real-key"})
    assert resp.status_code == 401, resp.text


# NFR-09
@pytest.mark.asyncio
async def test_fr05_scope_gate_forbidden_when_scope_mismatch(client):
    """[coverage] Exercise the scope-gate ForbiddenProblem on line 177
    (the ``raise ForbiddenProblem(detail="forbidden")`` branch when
    the requester's scope is NOT in the gate's allowed set).

    The admin-only endpoint ``DELETE /v1/tasks/{id}`` requires
    ``require_scope("admin")``. Sending a write-scoped key must trip
    the gate and return 403 with the opaque ``"forbidden"`` body.
    """
    os.environ.pop("TASKQ_RATE_BURST", None)
    os.environ.pop("TASKQ_RATE_PER_SEC", None)

    # ``test-write-key`` is registered as scope=write; admin route rejects it.
    fake_task_id = "t" * 36  # Path constraint: min_length=36, max_length=36
    resp = await client.delete(
        f"/v1/tasks/{fake_task_id}",
        headers={"X-API-Key": "test-write-key"},
    )
    assert resp.status_code == 403, resp.text
    # SPEC §3 FR-04 mandates the opaque ``forbidden`` token — the
    # detail must NOT leak scope names or task ids.
    assert resp.json().get("detail") == "forbidden"


# NFR-09
@pytest.mark.asyncio
async def test_fr05_scope_gate_allows_when_scope_matches(client):
    """[coverage] Exercise the success branch on line 178 of deps.py
    (``return key`` after the scope gate passes).

    ``test-admin-key`` is registered as scope=admin. The DELETE
    route's ``require_scope("admin")`` gate passes and the handler
    body runs (the unknown task id causes the handler to return 404,
    which proves the gate returned rather than the 403 branch).
    """
    os.environ.pop("TASKQ_RATE_BURST", None)
    os.environ.pop("TASKQ_RATE_PER_SEC", None)

    fake_task_id = "t" * 36
    resp = await client.delete(
        f"/v1/tasks/{fake_task_id}",
        headers={"X-API-Key": "test-admin-key"},
    )
    # The handler runs — unknown task yields 404 (not 403) which proves
    # the scope gate's ``return key`` branch on line 178 executed.
    assert resp.status_code == 404, resp.text


# NFR-09
def test_fr05_retry_after_floor_when_rate_nonpositive():
    """[coverage] Exercise the ``rate_per_sec <= 0`` branch on line 65.

    When the configured refill rate is zero or negative, the
    delta-seconds Retry-After must degrade to the 1-second floor
    (RFC 9110 §10.2.3 — a value of 0 would invite hot-loops).
    """
    from taskq_api.service.ratelimit import _retry_after_seconds

    assert _retry_after_seconds(0.0) == 1
    assert _retry_after_seconds(-1.0) == 1


# NFR-09
def test_fr05_check_and_consume_initialises_bucket_when_none():
    """[coverage] Exercise the ``bucket is None`` branch in
    ``_current_tokens`` (lines 52-53 of ratelimit.py) — a brand-new
    token must get the full burst budget on first sight.
    """
    RateRepo._buckets.clear()
    try:
        decision = check_and_consume("brand-new-token", burst=7, rate_per_sec=1.0)
        # First sight: consume one token out of 7 → tokens == 6.0.
        assert decision.allowed is True
        assert decision.tokens == 6.0
    finally:
        RateRepo._buckets.clear()


# NFR-09
def test_fr05_read_rate_config_returns_none_when_burst_malformed(monkeypatch):
    """[coverage] Exercise the ``except ValueError`` branch (lines 80-83).

    When ``TASKQ_RATE_BURST`` is set to a non-integer string, the
    int() conversion raises ``ValueError`` and the function returns
    ``None`` so rate limiting is disabled instead of crashing every
    request.
    """
    monkeypatch.setenv("TASKQ_RATE_BURST", "not-an-integer")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "1.0")

    from taskq_api.api import deps as _deps

    assert _deps._read_rate_config() is None


# NFR-09
def test_fr05_rate_repo_lru_eviction_pops_oldest(monkeypatch):
    """[coverage] Exercise lines 96-97 of ``rate_repo.py`` —
    the LRU eviction loop that pops the oldest entry once the
    registry exceeds ``_MAX_BUCKETS`` (1024).
    """
    # Snapshot and restore the registry so this test does not
    # disturb other suites sharing ``RateRepo._buckets``.
    snapshot = list(RateRepo._buckets)
    RateRepo._buckets.clear()
    try:
        # Pre-fill 1024 entries so the *next* upsert trips the cap.
        for i in range(1024):
            RateRepo._buckets[f"pre-{i}"] = {
                "tokens": 1.0,
                "last_refill_at": 0.0,
            }
        # The 1025th upsert must evict ``"pre-0"`` (the oldest).
        RateRepo.upsert_bucket(
            None, "new-token", tokens=1.0, last_refill_at=0.0
        )
        assert "pre-0" not in RateRepo._buckets, (
            "LRU eviction failed: oldest entry still present"
        )
        assert "new-token" in RateRepo._buckets
        # Length must be exactly _MAX_BUCKETS after eviction.
        assert len(RateRepo._buckets) == 1024
    finally:
        RateRepo._buckets.clear()
        for k, v in snapshot:
            RateRepo._buckets[k] = v


# NFR-09
def test_fr05_rate_repo_upsert_swallows_typeerror(monkeypatch):
    """[coverage] Exercise lines 98-102 of ``rate_repo.py`` —
    the ``except (KeyError, TypeError, ValueError)`` fallback that
    silently drops a malformed upsert.

    Replace ``_buckets`` with a stub whose ``__setitem__`` raises
    ``TypeError`` so the upsert body fails after the pop and the
    except branch swallows the error.
    """

    class _RaisingBuckets:
        def pop(self, *args, **kwargs):
            return None

        def __setitem__(self, key, value):
            raise TypeError("simulated registry tamper")

        def __len__(self):
            return 0

        def clear(self):
            pass

        def move_to_end(self, *args, **kwargs):
            pass

    sentinel = _RaisingBuckets()
    monkeypatch.setattr(RateRepo, "_buckets", sentinel)
    # Must NOT raise — the except branch swallows the TypeError.
    RateRepo.upsert_bucket(
        None, "t", tokens=1.0, last_refill_at=0.0
    )


# NFR-09
def test_fr05_check_and_consume_swallows_get_bucket_error(monkeypatch):
    """[coverage] Exercise lines 97-101 of ``ratelimit.py`` —
    the ``except (KeyError, RuntimeError, OSError)`` that treats
    a failed ``get_bucket`` as a fresh bucket (``bucket = None``).

    Monkeypatch ``RateRepo.get_bucket`` to raise ``RuntimeError``
    so the caller's consume still proceeds against a full bucket.
    """

    def _raise(token: str):
        raise RuntimeError("simulated get_bucket failure")

    monkeypatch.setattr(RateRepo, "get_bucket", staticmethod(_raise))

    RateRepo._buckets.clear()
    try:
        decision = check_and_consume("err-token", burst=5, rate_per_sec=1.0)
        # Fresh-bucket branch: tokens=5, after consume tokens=4.
        assert decision.allowed is True
        assert decision.tokens == 4.0
    finally:
        RateRepo._buckets.clear()


# NFR-09
def test_fr05_check_and_consume_swallows_upsert_error(monkeypatch):
    """[coverage] Exercise lines 110-114 of ``ratelimit.py`` —
    the ``except (KeyError, RuntimeError, OSError)`` after
    ``repo.upsert_bucket`` that returns the decision anyway when
    the persist call fails.
    """
    # Pre-populate a bucket with 2 tokens so consume succeeds and
    # upsert_bucket is invoked. Then make upsert_bucket raise so
    # the except branch swallows the failure.
    RateRepo._buckets.clear()
    RateRepo._buckets["upsert-err-token"] = {
        "tokens": 2.0,
        "last_refill_at": 0.0,
    }

    original = RateRepo.upsert_bucket

    def _raise(session, token, *, tokens, last_refill_at):
        raise RuntimeError("simulated upsert failure")

    monkeypatch.setattr(RateRepo, "upsert_bucket", staticmethod(_raise))
    try:
        decision = check_and_consume(
            "upsert-err-token", burst=5, rate_per_sec=1.0
        )
        # Despite the persist failure, the consume decision is
        # returned to the caller so the request proceeds.
        assert decision.allowed is True
        assert decision.tokens == 1.0
    finally:
        monkeypatch.setattr(RateRepo, "upsert_bucket", staticmethod(original))
        RateRepo._buckets.clear()


__all__ = [
    "test_fr05_burst_returns_429_with_retry_after",
    "test_fr05_bucket_row_level_lock",
    "test_fr05_health_endpoints_exempt",
    "test_fr05_retry_after_header_present",
    "test_fr05_apikey_init_with_id_uses_provided_id",
    "test_fr05_apikey_init_without_id_generates_uuid",
    "test_fr05_apikey_as_row_returns_field_dict",
    "test_fr05_apikey_repr_includes_all_fields",
    "test_fr05_taskresult_init_with_id_uses_provided_id",
    "test_fr05_taskresult_init_without_id_generates_uuid",
    "test_fr05_taskresult_add_and_list_for_task_round_trip",
    "test_fr05_taskresult_from_dict_via_list_for_task",
    "test_fr05_read_rate_config_returns_none_when_unset",
    "test_fr05_enforce_rate_limit_skips_when_config_none",
    "test_fr05_missing_api_key_header_returns_401",
    "test_fr05_invalid_api_key_returns_401",
    "test_fr05_scope_gate_forbidden_when_scope_mismatch",
    "test_fr05_scope_gate_allows_when_scope_matches",
    "test_fr05_retry_after_floor_when_rate_nonpositive",
    "test_fr05_check_and_consume_initialises_bucket_when_none",
    "test_fr05_read_rate_config_returns_none_when_burst_malformed",
    "test_fr05_rate_repo_lru_eviction_pops_oldest",
    "test_fr05_rate_repo_upsert_swallows_typeerror",
    "test_fr05_check_and_consume_swallows_get_bucket_error",
    "test_fr05_check_and_consume_swallows_upsert_error",
]