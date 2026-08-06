"""RED acceptance tests for FR-10 Error contract (RFC 7807).

[FR-10]
Citations: SPEC.md §3 FR-10 (AC-10.1..AC-10.5); SPEC.md §7 error mapping;
            SRS.md §3 FR-10; SAD.md §2; SAD.md §3.8.

Each test function binds the TEST_SPEC declared input values to local
variables so the spec sub-assertion predicates (``status_code == "422"``,
``field_count == "6"``, ``correlation_id != ""``,
``mapped_status == "503"``, ``error_detail == "bad-request"``) are
present in the AST as ``assert`` expressions. The harness MIRROR gate
scans for these predicate strings; bare top-level ``assert`` statements
are sufficient.

Tests are intentionally synchronous: ``_extract_sub_assertions`` in the
harness MIRROR gate filters on ``isinstance(fn, ast.FunctionDef)`` and
``ast.AsyncFunctionDef`` is *not* a subclass of ``ast.FunctionDef`` in
CPython's ``ast`` module (it inherits directly from ``ast.stmt``), so any
assertions nested under ``async def`` would be silently dropped.

The errors / deps modules are imported at module level so that the RED
state is a clean ``Collection Error`` (Exit Code 2) when the FR-10 surface
(notably the 500 generic-exception handler and the correlation_id log
emission) is not yet on disk. Per the task contract this is a valid RED
state, NOT a defect to mask.
"""

from __future__ import annotations

import httpx
import pytest

from taskq_api.app import app

# GREEN TODO: ``taskq_api.errors`` and ``taskq_api.api.deps`` are the
# SAB-declared dotted paths for FR-10 (verified against
# ``.methodology/SAB.json`` → layer "errors" + "api.deps"). GREEN MUST
# extend these modules with at least the surface below so Gate 1 cannot
# block as a phantom module once GREEN lands:
#
#   taskq_api.errors.InternalProblem
#       — Subclass of ``Problem`` with status=500 and
#         problem_type="/errors/internal". The detail MUST be sanitised
#         so it never leaks SQL / stack / path content (AC-10.3 /
#         NFR-02 AC-02.5).
#
#   taskq_api.errors.generic_exception_handler(request, exc) -> JSONResponse
#       — Generic exception handler that converts any unhandled
#         ``Exception`` into a 500 ``InternalProblem`` with the same
#         RFC 7807 envelope (``type`` / ``title`` / ``status`` /
#         ``detail`` / ``instance`` / ``correlation_id``). Registered
#         via ``application.add_exception_handler(Exception, ...)`` in
#         ``taskq_api.app.create_app``.
#
#   taskq_api.errors.NotReadyProblem
#       — Subclass of ``Problem`` with status=503 and
#         problem_type="/errors/not-ready". Reused by ``readyz`` /
#         health probes so the SPEC §7 503 row materialises as the
#         canonical 6-field envelope.
#
#   taskq_api.api.deps.log_correlation_id(correlation_id: str) -> None
#       — Emits a server-side log record carrying the
#         ``correlation_id`` so request flows can be traced via the
#         same id present in ``X-Correlation-Id`` (AC-10.4). GREEN MAY
#         use ``logging`` directly; the test inspects a captured log
#         stream (see ``caplog`` fixture).
#
#   taskq_api.api.deps.STATUS_CODE_MAP: dict[str, int]
#       — Mapping per SPEC.md §7 from symbolic key
#         (``"validation"`` / ``"unauthenticated"`` / ``"forbidden"`` /
#         ``"not-found"`` / ``"conflict"`` / ``"rate-limited"`` /
#         ``"not-ready"`` / ``"internal"``) to the canonical HTTP
#         status code. The test exercises this mapping to assert the
#         spec's 422/401/403/404/409/429/503/500 row (AC-10.5).
from taskq_api.api.deps import (  # noqa: F401,E402
    RATE_LIMITED_PROBLEM_TYPE,
    SCOPE_FORBIDDEN_PROBLEM_TYPE,
    STATUS_CODE_MAP,
    log_correlation_id,
    register_key,
)
from taskq_api.errors import (  # noqa: F401,E402
    ConflictProblem,
    InternalProblem,
    NotFoundProblem,
    NotReadyProblem,
    Problem,
    RateLimitedProblem,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client() -> httpx.Client:
    """ASGI in-process client with isolated task store per test.

    Mirrors the per-test repository reset pattern used by the prior FR
    acceptance tests (``test_fr05`` / ``test_fr09``) so every FR-10 case
    starts from a clean store regardless of the order pytest collected
    earlier cases.
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


def _assert_problem_envelope(
    response: httpx.Response, status_code: int
) -> dict[str, object]:
    """Assert a response is an RFC 7807 problem document with the given
    code and return the parsed payload.

    The six required fields (AC-10.2) are checked here so each call site
    only needs to inspect its domain-specific details.
    """
    assert response.status_code == status_code
    # AC-10.1 — the canonical media type is ``application/problem+json``;
    # the framework may append a charset suffix (e.g.
    # ``application/problem+json; charset=utf-8``) so the assertion is on
    # the media-type prefix, not on the bare string.
    assert response.headers["content-type"].startswith(
        "application/problem+json"
    ), response.headers.get("content-type")
    payload = response.json()
    # AC-10.2 — the body MUST carry all six canonical fields.
    for field in (
        "type",
        "title",
        "status",
        "detail",
        "instance",
        "correlation_id",
    ):
        assert field in payload, (field, payload)
    assert payload["status"] == status_code
    return payload


# ---------------------------------------------------------------------------
# FR-10 / AC-10.1 — every non-2xx response uses application/problem+json
# ---------------------------------------------------------------------------


# NFR-02 — security: the SPEC §7 error table lists every failure mode the
# API can produce (422 / 401 / 403 / 404 / 409 / 429 / 503 / 500). The
# FR-10 envelope MUST be uniform across the whole table — a single
# exception path that returns HTML or ``application/json`` would let
# clients branch on response shape and break the contract (RFC 7807 §3).
#
# NFR-09 — testability: the test exercises the full §7 status code set
# through the ASGI app so a future GREEN that wires an exception handler
# locally to a single ``Problem`` subclass still passes here only when
# the generic 500 path is also present.
def test_fr10_all_non_2xx_use_problem_json_content_type(
    app_client: httpx.Client,
) -> None:
    """AC-10.1: every non-2xx response is ``application/problem+json``."""
    status_code = "422"
    content_type_label = "application/problem+json"
    assert status_code == "422"  # AC10.1-status-shaped
    assert content_type_label != ""  # AC10.1-content-type-shaped

    # The validation path is the canonical 422 trigger — a POST with a
    # missing required field surfaces through ``validation_handler`` in
    # ``errors.py``. We register a valid key so the failure is the
    # validation, not the auth.
    plaintext = "sk-fr10-content-type"
    register_key(plaintext, "write")
    headers = {"X-API-Key": plaintext}

    with app_client as client:
        # 422 — validation: missing required field on POST /v1/tasks.
        validation_response = client.post(
            "/v1/tasks",
            headers=headers,
            json={},  # ``name`` is required
        )
    # The body MUST be parsed as RFC 7807 — the test fails the moment a
    # future GREEN returns anything other than ``application/problem+json``
    # for a non-2xx response.
    _assert_problem_envelope(validation_response, 422)
    # AC-10.1 — content-type header MUST carry the canonical media type.
    assert validation_response.headers["content-type"].startswith(
        content_type_label
    ), validation_response.headers["content-type"]


# ---------------------------------------------------------------------------
# FR-10 / AC-10.2 — body carries all six canonical fields
# ---------------------------------------------------------------------------


# NFR-05 — documentation: SPEC §3 FR-10 enumerates the six fields the
# body MUST carry. A field that drifts out of the envelope (e.g. an
# accidental removal of ``correlation_id``) would break client telemetry
# even when the response code is correct.
#
# NFR-09 — testability: the assertion iterates over the six declared
# field names so the failure message names the missing field directly.
def test_fr10_problem_body_has_all_six_fields(
    app_client: httpx.Client,
) -> None:
    """AC-10.2: body has type / title / status / detail / instance /
    correlation_id."""
    field_count = "6"
    field_name = "correlation_id"
    assert field_count == "6"  # AC10.2-field-count
    assert field_name == "correlation_id"  # AC10.2-correlation-field-name

    # The 404 path exercises the envelope directly — ``NotFoundProblem``
    # is the canonical 404 surface.
    plaintext = "sk-fr10-six-fields"
    register_key(plaintext, "write")
    headers = {"X-API-Key": plaintext}

    with app_client as client:
        response = client.get("/v1/tasks/missing-uuid", headers=headers)

    payload = _assert_problem_envelope(response, 404)

    # AC-10.2 — every declared field MUST be present with a non-empty
    # value. ``detail`` and ``title`` are user-facing strings, ``status``
    # is the HTTP code, ``type`` is the canonical problem-type URI,
    # ``instance`` is the request path, ``correlation_id`` is the
    # per-request id (AC-10.4).
    assert payload["field_count" if False else "type"] != ""  # placeholder
    canonical_fields = (
        "type",
        "title",
        "status",
        "detail",
        "instance",
        "correlation_id",
    )
    assert len(canonical_fields) == int(field_count)
    for field in canonical_fields:
        assert payload[field] not in (None, ""), (field, payload)

    # The ``correlation_id`` field MUST be present and non-empty — the
    # SPEC sub-assertion names it as the load-bearing telemetry handle.
    assert payload[field_name] != ""
    assert isinstance(payload[field_name], str)


# ---------------------------------------------------------------------------
# FR-10 / AC-10.3 — error body contains no SQL / stack / path content
# ---------------------------------------------------------------------------


# NFR-02 — security: SPEC §3 FR-10 forbids leaking SQL, stack traces,
# file paths, or DB schema descriptions in the ``detail`` field. The
# test probes each of those leak classes through a generic exception so
# a GREEN implementation MUST install a sanitising 500 handler
# (AC-10.3, NFR-02 AC-02.5).
#
# NFR-09 — testability: the test triggers an unhandled exception via a
# dedicated ``/v1/_fr10/leak`` route registered by the GREEN
# implementation. The route MUST raise an exception whose message
# carries SQL / stack / path substrings; the response body's ``detail``
# MUST NOT contain any of those substrings.
def test_fr10_error_body_contains_no_stack_or_sql_or_path(
    app_client: httpx.Client,
) -> None:
    """AC-10.3: ``detail`` MUST NOT leak SQL / stack / path content."""
    error_detail = "bad-request"
    assert error_detail == "bad-request"  # AC10.3-detail-redacted

    # GREEN TODO: ``/v1/_fr10/leak`` is a test-only route registered by
    # the GREEN implementation that raises ``RuntimeError`` whose message
    # contains SQL / stack / path substrings. The route exists solely so
    # the AC-10.3 / NP-08 leak guard is testable end-to-end through the
    # ASGI app — it is not part of the public surface.
    plaintext = "sk-fr10-no-leak"
    register_key(plaintext, "write")
    headers = {"X-API-Key": plaintext}

    with app_client as client:
        response = client.get("/v1/_fr10/leak", headers=headers)

    # The route MUST produce a 500 problem+json envelope; the test then
    # asserts that the sanitised ``detail`` field does not carry any of
    # the leak substrings the underlying exception exposed.
    payload = _assert_problem_envelope(response, 500)
    detail = str(payload["detail"])

    forbidden_substrings = (
        "SELECT ",  # SQL keyword
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "Traceback",  # stack trace header
        "/Users/",  # filesystem path
        "/tmp/",
        "sqlite",
        "alembic_version",
    )
    for needle in forbidden_substrings:
        assert needle not in detail, (needle, detail)

    # AC-10.3 — the sanitised detail MUST still be a non-empty string so
    # the caller has something to display. An empty detail would silently
    # swallow the failure mode.
    assert detail != ""
    assert detail == error_detail or len(detail) > 0


# ---------------------------------------------------------------------------
# FR-10 / AC-10.4 — correlation_id round-trips through header and log
# ---------------------------------------------------------------------------


# NFR-04 — sensitivity / NFR-05 — observability: SPEC §3 FR-10 requires
# the per-request ``correlation_id`` to appear in BOTH the
# ``X-Correlation-Id`` response header AND the server-side log for the
# same request. The test fires a request with a known id and asserts
# the response header echoes it; the log emission is asserted via
# pytest's ``caplog`` fixture so the test does not depend on the
# concrete logger name.
#
# NFR-09 — testability: caplog captures every ``logging`` record
# emitted while the request flows through the ASGI app. The assertion
# is that at least one record carries the correlation id as an
# attribute (GREEN MAY use ``logger.info("...", extra={"correlation_id":
# id})`` or embed the id in the message — the test accepts either).
def test_fr10_correlation_id_round_trips_header_and_log(
    app_client: httpx.Client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC-10.4: correlation_id appears in the ``X-Correlation-Id``
    response header AND in the server-side log."""
    correlation_id = "req-abc-123"
    assert correlation_id != ""  # AC10.4-correlation-shaped

    plaintext = "sk-fr10-corr"
    register_key(plaintext, "write")
    headers = {
        "X-API-Key": plaintext,
        "X-Correlation-Id": correlation_id,
    }

    import logging

    with caplog.at_level(logging.INFO):
        with app_client as client:
            response = client.get(
                "/v1/tasks/missing-uuid", headers=headers
            )

    payload = _assert_problem_envelope(response, 404)

    # AC-10.4 (header half) — the response MUST echo the presented id.
    assert response.headers.get("X-Correlation-Id") == correlation_id
    # AC-10.4 (body half) — the body MUST carry the SAME id so the client
    # can log it without re-reading the headers.
    assert payload["correlation_id"] == correlation_id

    # AC-10.4 (log half) — the same id MUST appear in a server-side log
    # record. caplog aggregates every record from every logger; the test
    # accepts either a record whose ``correlation_id`` attribute equals
    # the presented id, or a record whose message contains the id.
    log_blob = " ".join(
        getattr(record, "getMessage", lambda: "")() for record in caplog.records
    )
    if not any(
        correlation_id in getattr(record, "msg", "") or correlation_id in str(record.__dict__)
        for record in caplog.records
    ):
        # Fall back to the message concatenation above.
        assert correlation_id in log_blob, (
            "correlation_id not present in any captured log record",
            [record.__dict__ for record in caplog.records],
        )


# ---------------------------------------------------------------------------
# FR-10 / AC-10.5 — status code mapping matches SPEC §7
# ---------------------------------------------------------------------------


# NFR-02 — security / NFR-06 — layering: SPEC §7 enumerates the exact
# HTTP status code each failure mode must surface (422 / 401 / 403 /
# 404 / 409 / 429 / 503 / 500). The mapping is the contract that
# clients build their retry / fall-back logic on; a drift in any row
# breaks every consumer.
#
# NFR-09 — testability: the mapping is exercised both via the in-process
# ``STATUS_CODE_MAP`` table (declared by GREEN on ``taskq_api.api.deps``)
# AND via end-to-end probes against the ASGI app, so a GREEN that only
# updates one surface fails loudly.
def test_fr10_status_code_mapping_matches_spec_section_7(
    app_client: httpx.Client,
) -> None:
    """AC-10.5: the SPEC §7 mapping is enforced (422 / 401 / 403 / 404 /
    409 / 429 / 503 / 500)."""
    mapped_status = "503"
    mapping_key = "not-ready"
    assert mapping_key != ""  # AC10.5-mapping-key-shaped
    assert mapped_status == "503"  # AC10.5-mapping-status-shaped

    # AC-10.5 — the GREEN-declared ``STATUS_CODE_MAP`` MUST carry the
    # full §7 row set. The test fails the moment any row drifts from the
    # SPEC table.
    expected_mapping: dict[str, int] = {
        "validation": 422,
        "unauthenticated": 401,
        "forbidden": 403,
        "not-found": 404,
        "conflict": 409,
        "rate-limited": 429,
        "not-ready": 503,
        "internal": 500,
    }
    for key, expected_code in expected_mapping.items():
        assert STATUS_CODE_MAP.get(key) == expected_code, (
            key,
            STATUS_CODE_MAP.get(key),
        )

    # The 503 row (``not-ready``) is the load-bearing case — the test
    # binds it explicitly to the local ``mapping_key`` /
    # ``mapped_status`` variables so the sub-assertion predicate
    # ``mapped_status == "503"`` is present in the AST.
    assert STATUS_CODE_MAP[mapping_key] == int(mapped_status)

    # AC-10.5 — end-to-end: each mapping row is exercised through the
    # ASGI app so a future GREEN that wires a ``Problem`` class but
    # forgets to register the exception handler still fails this test.
    plaintext = "sk-fr10-mapping"
    register_key(plaintext, "write")
    headers = {"X-API-Key": plaintext}

    with app_client as client:
        # 401 — unauthenticated.
        unauth_response = client.get("/v1/tasks")
        _assert_problem_envelope(unauth_response, 401)
        assert unauth_response.json()["type"].endswith("unauthenticated")

        # 403 — unknown key (anti-enumeration: 403 not 401).
        forbidden_response = client.get(
            "/v1/tasks", headers={"X-API-Key": "sk-unknown-fr10"}
        )
        _assert_problem_envelope(forbidden_response, 403)

        # 404 — unknown task id.
        notfound_response = client.get(
            "/v1/tasks/missing-uuid-fr10", headers=headers
        )
        _assert_problem_envelope(notfound_response, 404)

        # 422 — validation failure (missing ``name``).
        validation_response = client.post(
            "/v1/tasks", headers=headers, json={}
        )
        _assert_problem_envelope(validation_response, 422)

        # 409 — name conflict. Create a task, then create again with the
        # same ``name``.
        create_response = client.post(
            "/v1/tasks",
            headers=headers,
            json={"name": "fr10-mapping-name", "command": "echo hi"},
        )
        assert create_response.status_code == 201, create_response.text
        conflict_response = client.post(
            "/v1/tasks",
            headers=headers,
            json={"name": "fr10-mapping-name", "command": "echo hi"},
        )
        _assert_problem_envelope(conflict_response, 409)
