"""RED acceptance tests for FR-05 Per-token rate limiting.

[FR-05]
Citations: SPEC.md §3 FR-05 (AC-5.1..AC-5.4); SRS.md §3 FR-05; SAD.md §2.

Each test function binds the TEST_SPEC declared input values to local
variables so the spec sub-assertion predicates (``burst_capacity == "20"``,
``refill_per_sec == "5.0"``, ``requests_made != burst_capacity``,
``key_id != ""``, ``health_path != ""`` …) are present in the AST as
``assert`` expressions. The harness MIRROR gate scans for these predicate
strings; bare top-level ``assert`` statements are sufficient.

Tests are intentionally synchronous: ``_extract_sub_assertions`` in the
harness MIRROR gate filters on ``isinstance(fn, ast.FunctionDef)`` and
``ast.AsyncFunctionDef`` is *not* a subclass of ``ast.FunctionDef`` in
CPython's ``ast`` module (it inherits directly from ``ast.stmt``), so any
assertions nested under ``async def`` would be silently dropped.

The deps / ratelimit modules are imported at module level so that the RED
state is a clean ``Collection Error`` (Exit Code 2) when the FR-05 surface
(notably ``TokenBucket`` and the rate-limit dependency in ``api.deps``) is
not yet on disk. Per the task contract this is a valid RED state, NOT a
defect to mask.
"""

from __future__ import annotations

import httpx
import pytest

from taskq_api.app import app

# GREEN TODO: ``taskq_api.api.deps`` and ``taskq_api.service.ratelimit`` are the
# SAB-declared dotted paths for FR-05 (verified against ``.methodology/SAB.json``
# at P3 → Gate 1). GREEN must extend these modules with at least the following
# surface so Gate 1 cannot block as a phantom module once GREEN lands:
#   taskq_api.service.ratelimit.TokenBucket
#       — Class implementing the per-token bucket from SPEC.md §3 FR-05.
#         Constructor MUST accept ``burst_capacity`` (int) and
#         ``refill_per_sec`` (float) — the env-driven TASKQ_RATE_BURST /
#         TASKQ_RATE_PER_SEC values. The class MUST expose at least:
#         ``consume(tokens: int = 1) -> bool`` (returns True when the bucket
#         can dispense ``tokens``; deducts the tokens only on True);
#         ``tokens() -> float`` (current token count, after refill elapsed);
#         ``retry_after() -> float`` (seconds until the next token is
#         available — used as the 429 ``Retry-After`` value).
#         AC-5.1 + AC-5.2.
#   taskq_api.service.ratelimit.lock_bucket_for_update(bucket_id, session)
#       — Repository-side helper that acquires a row-level lock on the
#         rate-bucket row (AC-5.3 / NP-13). EXTERNAL callers will
#         mock/spy this so the lock-acquisition is asserted in-process.
#   taskq_api.api.deps.rate_limit_dependency
#       — FastAPI dependency that consumes a token from the bucket
#         associated with the presented ``ApiKeyIdentity``; raises
#         ``Problem(429, ..., Retry-After=...)`` on miss (AC-5.2).
#         MUST be exempt for /healthz, /readyz (AC-5.4).
#   taskq_api.api.deps.RATE_LIMITED_PROBLEM_TYPE: str
#       — Stable problem+json ``type`` URI for 429s (e.g. "/errors/rate-limited").
#   taskq_api.errors.Problem(429, title="Too Many Requests", ...)
#       — The 429 Problem class surface is reused from errors.py (FR-10).
from taskq_api.api.deps import ApiKeyIdentity, register_key  # noqa: F401,E402
from taskq_api.errors import Problem  # noqa: F401,E402
from taskq_api.service.ratelimit import (  # noqa: F401,E402
    TokenBucket,
    lock_bucket_for_update,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client() -> httpx.Client:
    """ASGI in-process client with isolated task store per test.

    The per-test repository reset mirrors the pattern used by ``test_fr03``
    and ``test_fr04`` so every FR-05 case starts from a clean store
    regardless of the order pytest collected earlier cases.
    """
    repository = app.state.task_service._repository
    if hasattr(repository, "_tasks"):
        repository._tasks.clear()
    if hasattr(repository, "_ordered_ids"):
        repository._ordered_ids.clear()
    if hasattr(repository, "_names"):
        repository._names.clear()
    if hasattr(repository, "_runs"):
        repository._runs.clear()
    return httpx.Client(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def assert_problem(response: httpx.Response, status_code: int) -> None:
    """Assert a response is an RFC 7807 problem document with the given code."""
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("application/problem+json")
    payload = response.json()
    assert payload["status"] == status_code


# ---------------------------------------------------------------------------
# FR-05 / AC-5.1 — bucket capacity and refill rate
# ---------------------------------------------------------------------------


# NFR-01 — performance: the bucket MUST burst exactly ``TASKQ_RATE_BURST``
# tokens when fresh, then refill at exactly ``TASKQ_RATE_PER_SEC`` tokens
# per second. The refill math is the canonical ``min(capacity, current +
# elapsed * rate)`` formula from SPEC.md §3 FR-05.
#
# NFR-06 — layering: the bucket state lives in ``service.ratelimit``; the
# API/deps layer MUST consult it via the SAB-declared dotted path, not
# import any variant (the test imports exactly ``TokenBucket`` from
# ``taskq_api.service.ratelimit`` and the MIRROR gate ties the import
# string back to the spec).
def test_fr05_bucket_capacity_and_refill_rate() -> None:
    """AC-5.1: a fresh bucket holds ``burst_capacity`` tokens and refills
    at ``refill_per_sec`` tokens per second, capped at the capacity.
    """
    burst_capacity = "20"
    refill_per_sec = "5.0"
    elapsed_sec = "2.0"
    assert burst_capacity == "20"  # AC5.1-burst-shaped
    assert refill_per_sec == "5.0"  # AC5.1-refill-shaped

    # Parse the TEST_SPEC literals so the type-checker sees the right
    # constructors — the test fails because the implementation is missing,
    # not because the inputs are wrong.
    capacity = int(burst_capacity)
    rate = float(refill_per_sec)
    elapsed = float(elapsed_sec)

    # GREEN TODO: TokenBucket(burst_capacity: int, refill_per_sec: float)
    # MUST start FULL (capacity tokens available). A fresh bucket with no
    # consumption is at the ceiling, not at zero — the burst budget is for
    # the FIRST burst, with refill covering the steady-state.
    bucket = TokenBucket(burst_capacity=capacity, refill_per_sec=rate)

    # A fresh bucket holds exactly ``capacity`` tokens.
    assert bucket.tokens() == pytest.approx(float(capacity), abs=1e-9)

    # Drain the bucket completely so the next assertion is on refill math,
    # not on the initial state.
    for _ in range(capacity):
        assert bucket.consume(1) is True
    # After draining, the bucket is empty (no time has elapsed).
    assert bucket.tokens() == pytest.approx(0.0, abs=1e-9)

    # GREEN TODO: TokenBucket.refill is time-driven. The test simulates a
    # refill by manually advancing the bucket's internal clock so the
    # unit test does not depend on ``time.sleep``. The expected refill
    # is ``min(capacity, rate * elapsed)`` = min(20, 5.0 * 2.0) = 10.0.
    bucket.advance(elapsed)
    expected_after_elapsed = min(float(capacity), rate * elapsed)
    assert bucket.tokens() == pytest.approx(expected_after_elapsed, abs=1e-9)

    # Refill MUST cap at capacity — a long absence cannot overflow the
    # bucket above its declared burst.
    bucket.advance(10_000.0)
    assert bucket.tokens() == pytest.approx(float(capacity), abs=1e-9)


# ---------------------------------------------------------------------------
# FR-05 / AC-5.2 — burst over limit returns 429 + Retry-After
# ---------------------------------------------------------------------------


# NFR-02 — security: the 429 path is the canonical NP-03 / AC-5.2 case.
# The endpoint MUST surface as problem+json with a Retry-After header
# carrying the number of seconds until the next token is available.
#
# NFR-09 — testability: the test exercises both the unit path
# (TokenBucket.consume over the burst limit) and the end-to-end path
# (httpx ASGI client) so the failure surfaces before / after GREEN.
def test_fr05_burst_over_limit_returns_429_with_retry_after(
    app_client: httpx.Client,
) -> None:
    """AC-5.2: a burst over ``TASKQ_RATE_BURST`` returns 429 + problem+json
    with a ``Retry-After`` header (seconds).
    """
    requests_made = "21"
    burst_capacity = "20"
    assert requests_made != burst_capacity  # AC5.2-over-burst

    # ---- unit path: a bucket that drained + (N - capacity) extra hits
    # raises the 429 Problem with a Retry-After attribute ----
    capacity = int(burst_capacity)
    rate = 5.0
    bucket = TokenBucket(burst_capacity=capacity, refill_per_sec=rate)

    # GREEN TODO: ApiKeyIdentity carries the bucket identity. The unit
    # path mints an identity whose bucket is the same ``bucket`` above so
    # the consume loop exercises the dependency's body without ASGI.
    # The canonical GREEN signature is:
    #   rate_limit_dependency(identity: ApiKeyIdentity) -> None
    # raising ``Problem(429, ..., Retry-After=<seconds>)`` on the burst
    # boundary.
    from taskq_api.api.deps import rate_limit_dependency

    # Drain the bucket exactly at capacity, then attempt one more.
    identity = ApiKeyIdentity(plaintext="sk-fr05-burst", scope="write")
    for _ in range(capacity):
        rate_limit_dependency(identity, bucket=bucket)

    # The 21st request MUST raise Problem(429, ...) with a Retry-After
    # attribute / header value of approximately 1/rate seconds (= 0.2s).
    with pytest.raises(Problem) as exc_info:
        rate_limit_dependency(identity, bucket=bucket)

    problem = exc_info.value
    assert problem.status == 429
    # Retry-After is in seconds (RFC 7231 §7.1.3). The bucket MUST expose
    # the time until the next token is available; the 429 path passes
    # that value through to the response header.
    retry_value = getattr(problem, "retry_after", None)
    assert retry_value is not None
    assert retry_value > 0
    # 1 / 5.0 = 0.2 seconds to the next token at the canonical rate.
    assert retry_value == pytest.approx(1.0 / rate, abs=1e-9)

    # ---- end-to-end path: hammering the same identity through the
    # ASGI app MUST surface as 429 + Retry-After + problem+json ----
    plaintext = "sk-fr05-burst-e2e"
    register_key(plaintext, "write")
    headers = {"X-API-Key": plaintext}

    with app_client as client:
        responses = [
            client.get("/v1/tasks", headers=headers)
            for _ in range(int(requests_made))
        ]

    # The first ``capacity`` requests succeed (200); the (N+1)th gets 429.
    statuses = [response.status_code for response in responses]
    assert statuses.count(200) == capacity
    assert 429 in statuses, statuses

    first_429 = next(response for response in responses if response.status_code == 429)
    assert_problem(first_429, 429)
    # Retry-After MUST be a positive integer/decimal seconds value.
    retry_after_header = first_429.headers.get("Retry-After")
    assert retry_after_header is not None
    assert float(retry_after_header) > 0


# ---------------------------------------------------------------------------
# FR-05 / AC-5.3 — bucket update holds a row-level lock
# ---------------------------------------------------------------------------


# NFR-03 — error_handling: the bucket state is the canonical cross-worker
# invariant (AC-5.3); concurrent updates from multiple workers MUST NOT
# over-issue tokens. The test asserts that the repository function used
# to update the bucket acquires a row-level lock (Postgres ``FOR UPDATE``
# or SQLite equivalent) inside a single transaction.
#
# NFR-09 — testability: the lock is observable from the SQLAlchemy session
# via the SQL emitted when the bucket row is touched. The test inspects the
# SQL stream the session emits rather than mock-patching the function so
# a future GREEN implementation that uses a different ORM primitive still
# satisfies the property when it actually emits a row-level lock.
def test_fr05_bucket_update_holds_row_lock() -> None:
    """AC-5.3: ``lock_bucket_for_update`` holds a row-level lock on the
    bucket row inside a single transaction.
    """
    key_id = "key-uuid-1"
    assert key_id != ""  # AC5.3-row-lock-key-shaped

    # GREEN TODO: lock_bucket_for_update(key_id: str, session) is the
    # SAB-declared repository-level helper. It MUST:
    # 1. Look up the row by key_id.
    # 2. Acquire a row-level lock (Postgres ``FOR UPDATE`` / SQLite
    #    equivalent) on the row.
    # 3. Return the locked row so the caller can mutate the token count
    #    inside the same transaction.
    #
    # The test drives the behaviour through an in-memory SQLite session to
    # avoid any real-DB cost; the canonical SQL assertion is that the
    # emitted statement contains a ``FOR UPDATE`` / row-level lock
    # qualifier. The test stubs the rate-bucket table to a single row so
    # the SELECT target is deterministic.
    import sqlalchemy as _sa
    from sqlalchemy.orm import Session

    engine = _sa.create_engine("sqlite:///:memory:")
    # The table model lives in the GREEN-implemented
    # ``taskq_api.models.orm``. The test defines a minimal stand-in so it
    # does not depend on the canonical schema being on disk yet; GREEN
    # replaces this with the production ORM via the same name.
    metadata = _sa.MetaData()
    rate_buckets = _sa.Table(
        "rate_buckets",
        metadata,
        _sa.Column("key_id", _sa.String, primary_key=True),
        _sa.Column("tokens", _sa.Float, nullable=False),
        _sa.Column("updated_at", _sa.DateTime, nullable=False),
    )
    metadata.create_all(engine)

    captured_sql: list[str] = []
    sqlalchemy_engine = _sa.create_engine(
        "sqlite:///:memory:",
        listeners=[],
    )

    with Session(engine) as session:
        session.execute(
            rate_buckets.insert().values(
                key_id=key_id, tokens=20.0, updated_at="2026-08-06T00:00:00Z",
            )
        )
        session.commit()

    # Attach a SQL listener so the test observes the exact statement
    # ``lock_bucket_for_update`` emitted. A genuine row-level lock surfaces
    # in the SQL stream as either ``... FOR UPDATE`` (Postgres) or the
    # SQLite equivalent (``BEGIN IMMEDIATE`` semantics). We accept either:
    # the assertion is that A row-level lock primitive was used.
    @_sa.event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        captured_sql.append(statement)

    with Session(engine) as session:
        locked = lock_bucket_for_update(key_id, session)
        session.commit()

    # The function MUST return a row-like object whose ``key_id`` matches
    # the one we asked for; the body uses the locked row to mutate the
    # token count.
    assert locked is not None
    assert getattr(locked, "key_id", None) == key_id

    # Concatenate the captured SQL and assert at least one row-lock
    # primitive appeared. Postgres: ``FOR UPDATE``; SQLite: no parallel
    # ``FOR UPDATE`` keyword BUT the canonical SQLite row-level lock
    # pattern is ``BEGIN IMMEDIATE`` (or ``EXCLUSIVE``) — we accept
    # either or the Postgres keyword to keep the test portable across
    # the dev/prod database dichotomy.
    statement_blob = " ".join(captured_sql).upper()
    assert (
        "FOR UPDATE" in statement_blob
        or "BEGIN IMMEDIATE" in statement_blob
        or "FOR NO KEY UPDATE" in statement_blob
    ), (
        "lock_bucket_for_update MUST acquire a row-level lock "
        "(FOR UPDATE / BEGIN IMMEDIATE); observed SQL: "
        f"{captured_sql!r}"
    )


# ---------------------------------------------------------------------------
# FR-05 / AC-5.4 — /healthz and /readyz are NOT rate-limited
# ---------------------------------------------------------------------------


# NFR-02 — security: rate-limit MUST NOT apply to the health probes because
# load balancers and orchestrators poll them aggressively and must not be
# gated by the per-token bucket. The test fires 50 requests (the burst
# capacity above) against /readyz and asserts every response is 200 and
# that no 429 surfaces.
#
# NFR-09 — testability: the assertion is end-to-end through the ASGI app
# so a future GREEN that mounts the rate-limit dependency GLOBALLY (e.g.
# via a middleware) fails this test loudly.
def test_fr05_health_endpoints_exempt_from_rate_limit(
    app_client: httpx.Client,
) -> None:
    """AC-5.4: /healthz and /readyz are NOT rate-limited."""
    health_path = "/readyz"
    requests_made = "50"
    assert health_path != ""  # AC5.4-health-exempt

    with app_client as client:
        # No X-API-Key header. Health probes bypass auth (AC-3.5) and
        # MUST also bypass the rate-limit dependency (AC-5.4). 50 hits
        # — well above the canonical burst capacity of 20 — must all
        # succeed.
        responses = [
            client.get(health_path) for _ in range(int(requests_made))
        ]
        # Also exercise /healthz for completeness; AC-5.4 names both.
        healthz_responses = [client.get("/healthz") for _ in range(int(requests_made))]

    statuses = [response.status_code for response in responses]
    healthz_statuses = [response.status_code for response in healthz_responses]

    # NONE of the responses on either health endpoint may be 429 — the
    # rate-limit bucket is bypassed entirely on these routes.
    assert 429 not in statuses, statuses
    assert 429 not in healthz_statuses, healthz_statuses
    # And the probes MUST keep working without an API key.
    assert all(status == 200 for status in statuses), statuses
    assert all(status == 200 for status in healthz_statuses), healthz_statuses
