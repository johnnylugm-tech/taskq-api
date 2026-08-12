"""TDD-RED failing tests for FR-10 (錯誤契約 — RFC 7807 error contract).

These tests intentionally fail because the FR-10 surface — specifically
the ``instance`` field on the RFC 7807 problem envelope — is missing
from the source modules declared in the SAB. The SAB binds FR-10 to:

    taskq_api.errors            (the ProblemDetail exception hierarchy
                                + register_error_handlers() wiring)
    taskq_api.api.tasks         (the routes whose 4xx/5xx responses
                                must use the FR-10 envelope)
    taskq_api.api.deps          (auth/scope gate — raises AuthProblem /
                                ForbiddenProblem into the FR-10 envelope)

Per SPEC.md §3 FR-10, every non-2xx response MUST:

  - have ``Content-Type: application/problem+json``
  - carry a JSON body with the fields ``type`` (URI), ``title``,
    ``status``, ``detail``, ``instance``, ``correlation_id``
  - mirror ``correlation_id`` into the ``X-Correlation-Id`` response
    header
  - NOT leak internal structure into ``detail`` (no stack trace, no
    SQL fragments, no filesystem paths — NFR-02 / SPEC §8 #19)

Per the TEST_INVENTORY / TEST_SPEC catalog (FR-10), the four test
functions below cover the canonical acceptance criteria:

    AC1-no-stack               500 body does not contain "Traceback"
    AC1-no-sql                 500 body does not contain "SELECT"
    AC1-no-path                500 body does not contain "/usr/"
    AC2-field-type             body.type starts with "/errors/"
    AC2-field-title            body.title is non-empty
    AC2-field-status           body.status == response.status_code
    AC2-field-detail           body.detail is non-empty
    AC2-field-instance         body.instance is non-empty (MISSING — RED)
    AC2-field-correlation      body.correlation_id is a 36-char UUID
    AC3-header-set             response X-Correlation-Id == body.correlation_id
    AC4-content-type           Content-Type == "application/problem+json"

The TEST_SPEC names are the contract; spec-coverage-check uses exact
match. Do NOT rename these functions.
"""
from __future__ import annotations

import asyncio
import re

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Top-level imports — the FR-10 modules declared in the SAB already
# exist on disk, so there is no Collection Error at RED time. The RED
# state instead surfaces in ``test_fr10_problem_json_fields`` because
# the GREEN step has not yet added the ``instance`` field to the RFC
# 7807 envelope in ``taskq_api.errors._problem_envelope`` (the current
# envelope serialises only ``type``/``title``/``status``/``detail``/
# ``correlation_id``).
from taskq_api.api.deps import require_scope  # noqa: F401
from taskq_api.api.tasks import create_tasks_router  # noqa: F401
from taskq_api.errors import (  # noqa: F401
    AuthProblem,
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
    ProblemDetail,
    register_error_handlers,
)


# ---------------------------------------------------------------------------
# Test doubles — keep tests free of external side-effects.
# ---------------------------------------------------------------------------


def _stub_verify_key(_raw: str, _candidate: str) -> bool:
    """Stand-in for ``service.auth.verify_key`` that accepts any key.

    Lets the 4xx/5xx envelope tests authenticate without a real API
    key row in the in-process ``KeyRepo``. Production wiring moves
    verification into a real hash compare; this stub exists only so
    the test reaches the handler that raises the ProblemDetail.
    """
    return True


def _stub_scope_allows(_key: str, _allowed_scopes: frozenset[str]) -> bool:
    """Stand-in for ``service.auth.scope_allows`` that grants any scope."""
    return True


@pytest.fixture(autouse=True)
def _stub_auth_resolution(monkeypatch):
    """Bypass real auth so 4xx/5xx tests reach the handler that raises.

    FR-10 tests exercise the *envelope*, not the auth flow — patching
    ``verify_key`` + ``scope_allows`` keeps the route past the gate
    without registering a real ``KeyRepo`` row per test.
    """
    from taskq_api.service import auth as _auth_mod

    monkeypatch.setattr(_auth_mod, "verify_key", _stub_verify_key)
    monkeypatch.setattr(_auth_mod, "scope_allows", _stub_scope_allows)
    # Disable rate limiting so concurrent test runs are not throttled
    # by the in-process bucket (FR-05 — not the FR-10 surface under
    # test here).
    monkeypatch.delenv("TASKQ_RATE_BURST", raising=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _hit(app: FastAPI, method: str, path: str, **kwargs):
    """Send one HTTP request through ASGITransport and return the response.

    Tests do not share this helper's network state — each test owns its
    own ``AsyncClient`` instance so route-level side-effects (rate
    limiter, DB state) cannot leak across cases.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        return await getattr(ac, method)(path, **kwargs)


def _run(coro):
    """Drive one coroutine to completion under the test's event loop.

    Builds a fresh event loop per call so the helper works on Python
    3.10+ where ``asyncio.get_event_loop`` no longer auto-creates a
    loop on the main thread. The original implementation used
    ``asyncio.get_event_loop().run_until_complete`` to match the FR-09
    test suite — kept synchronous so the pytest runner surfaces RED
    failures as plain assertion errors rather than coroutine warnings.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Test cases from TEST_SPEC.md (do NOT rename)
# ---------------------------------------------------------------------------


# NFR-02 NFR-09
def test_fr10_500_body_no_leak(monkeypatch):
    """AC1-no-stack / AC1-no-sql / AC1-no-path. [FR-10][SPEC §8 #19]

    Triggering a 500 from inside a /v1/* handler MUST NOT echo the
    stack trace, raw SQL fragments, or filesystem paths into the
    response body (SPEC §8 #19 — "500 後檢查回應 body → 不含堆疊 /
    SQL / 檔案路徑"; NFR-02). The three negative assertions guard
    against the three leak channels that the production envelope
    MUST scrub.

    GREEN TODO: ``taskq_api.errors`` must register a generic
    ``Exception`` handler (in addition to the typed ``ProblemDetail``
    handler) so an unhandled ``RuntimeError`` raised inside a handler
    is converted to a 500 ``application/problem+json`` envelope whose
    ``detail`` is a sanitised static string. The current registration
    only catches ``ProblemDetail`` and ``RequestValidationError`` —
    any other exception falls through to FastAPI's default 500 path,
    which does not scrub the leak substrings.
    """
    from taskq_api.errors import InternalProblem

    app = FastAPI()
    register_error_handlers(app)

    @app.get("/_test_fr10_bomb")
    async def _bomb() -> None:
        # Stack trace would contain "Traceback"; SQL fragment would
        # contain "SELECT"; a /usr/ path would surface if the
        # exception message were echoed verbatim.
        raise RuntimeError(
            "Traceback (most recent call last):\n"
            "  File \"/usr/local/lib/python3.11/site-packages/x.py\", line 1, in <module>\n"
            "    SELECT * FROM secrets"
        )

    response = _run(_hit(app, "get", "/_test_fr10_bomb"))

    # The response must be 500 and the body must NOT leak any of the
    # three forbidden substrings. The static ``detail`` for
    # ``InternalProblem`` is the whitelisted "internal error" string
    # and never contains the original exception message.
    assert response.status_code == 500
    result_problem_body_str = response.text
    assert "Traceback" not in result_problem_body_str
    assert "SELECT" not in result_problem_body_str
    assert "/usr/" not in result_problem_body_str
    # Sanity check: the envelope is on the problem+json content type
    # so the test fails RED if the GREEN handler reverts to plain JSON.
    assert response.headers["content-type"].startswith("application/problem+json")
    # InternalProblem is the catch-all 500; the type URI must point at
    # the internal-error contract per SPEC §7.
    assert InternalProblem().type_uri == "/errors/internal"


# NFR-02 NFR-04 NFR-05 NFR-09
def test_fr10_problem_json_fields(monkeypatch):
    """AC2-field-* (six fields). [FR-10][SPEC §3][SPEC §7]

    A 422 validation error response MUST carry the full RFC 7807
    problem envelope:

        type           URI string starting with ``/errors/``
        title          non-empty short label
        status         integer matching the HTTP status code
        detail         non-empty human-readable explanation
        instance       non-empty URI identifying this occurrence
        correlation_id 36-char UUID4 string

    The current ``taskq_api.errors._problem_envelope`` does NOT
    include ``instance`` — that field is the canonical RED gap this
    test exposes. ``len(result_problem_instance_str) > 0`` MUST
    evaluate to ``True`` once GREEN adds the field; today the body has
    no ``instance`` key so the assertion fails (the JSON parses but
    the key is absent).

    GREEN TODO: ``_problem_envelope`` in ``taskq_api.errors`` must add
    an ``instance`` entry — the request path (``str(request.url.path)``
    or equivalent) — so every 4xx/5xx envelope carries it. The
    RequestValidationError path must mirror the same field set.
    """
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/_test_fr10_validate")
    async def _validate(payload: dict) -> dict:
        # ``name`` is missing — FastAPI raises ``RequestValidationError``
        # which the registered handler converts to a 422 problem+json.
        if "name" not in payload:
            raise NotFoundProblem(detail="payload missing required name")
        return payload

    response = _run(_hit(app, "post", "/_test_fr10_validate", json={}))

    result_response_status_code = response.status_code
    assert result_response_status_code == 422

    envelope = response.json()
    result_problem_type_str = envelope.get("type", "")
    result_problem_title_str = envelope.get("title", "")
    result_problem_status_int = envelope.get("status")
    result_problem_detail_str = envelope.get("detail", "")
    result_problem_instance_str = envelope.get("instance", "")
    result_problem_correlation_id_str = envelope.get("correlation_id", "")

    # AC2-field-type — must be a URI under our /errors/ namespace.
    assert result_problem_type_str.startswith("/errors/")
    # AC2-field-title — non-empty label.
    assert len(result_problem_title_str) > 0
    # AC2-field-status — must equal the HTTP status code.
    assert result_problem_status_int == result_response_status_code
    # AC2-field-detail — non-empty explanation.
    assert len(result_problem_detail_str) > 0
    # AC2-field-instance — non-empty URI of this occurrence. RED: the
    # current envelope serialises no ``instance`` key so this is the
    # failure surface that drives GREEN to add the field.
    assert len(result_problem_instance_str) > 0
    # AC2-field-correlation — 36-char UUID4.
    assert len(result_problem_correlation_id_str) == 36
    # A UUID4 hex looks like 8-4-4-4-12 with hyphens at fixed positions.
    assert re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        result_problem_correlation_id_str,
    )


# NFR-04 NFR-09
def test_fr10_x_correlation_id_header(monkeypatch):
    """AC3-header-set. [FR-10][SPEC §3 FR-10]

    Every non-2xx response MUST echo the same ``correlation_id`` into
    the ``X-Correlation-Id`` response header that it embeds in the
    JSON body — operators grep the body and the proxy logs against
    the same identifier (SPEC §3 FR-10 — "correlation_id 同時出現在
    回應 header X-Correlation-Id 與伺服器日誌"). The equality
    invariant ``correlation_id_field == correlation_id_header`` is
    the FR-10 property the production handler MUST guarantee.

    GREEN TODO: the typed ``ProblemDetail`` handler at
    ``taskq_api.errors._problem_exception_handler`` already sets
    ``X-Correlation-Id``; the GREEN step must extend that to the
    generic ``Exception`` handler and to the ``RequestValidationError``
    path so every non-2xx response — regardless of which handler
    rendered it — sets the header.
    """
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/_test_fr10_corr")
    async def _not_found() -> None:
        # Any non-2xx path is acceptable; ``NotFoundProblem`` is the
        # canonical 404 trigger for the FR-10 contract.
        raise NotFoundProblem(detail="correlation smoke")

    response = _run(_hit(app, "get", "/_test_fr10_corr"))

    # Status is 404 and the body is a problem+json envelope with a
    # correlation_id field.
    assert response.status_code == 404
    envelope = response.json()
    result_problem_correlation_id_str = envelope.get("correlation_id", "")
    assert len(result_problem_correlation_id_str) == 36

    # AC3-header-set — the response header MUST match the body field
    # (httpx lower-cases header names on read).
    result_response_correlation_header = response.headers.get("x-correlation-id")
    assert result_response_correlation_header is not None
    assert result_response_correlation_header == result_problem_correlation_id_str


# NFR-02 NFR-09
def test_fr10_content_type_problem_json(monkeypatch):
    """AC4-content-type. [FR-10][SPEC §3 FR-10][SPEC §164]

    Every non-2xx response MUST use the
    ``application/problem+json`` media type (SPEC §3 FR-10 / SPEC
    line 164). The 404 path through ``NotFoundProblem`` exercises the
    typed ``ProblemDetail`` handler — the canonical happy-path
    surface for FR-10.

    GREEN TODO: the typed ``ProblemDetail`` handler at
    ``taskq_api.errors._problem_exception_handler`` already sets
    ``media_type="application/problem+json"``; the GREEN step must
    ensure the generic ``Exception`` handler and the
    ``RequestValidationError`` path use the same media type so the
    invariant holds for every non-2xx response in the API.
    """
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/_test_fr10_ct")
    async def _missing() -> None:
        raise NotFoundProblem(detail="content-type smoke")

    response = _run(_hit(app, "get", "/_test_fr10_ct"))

    # Status is 404 — the trigger for AC4 is a typed problem.
    assert response.status_code == 404
    # AC4-content-type — exact match (httpx preserves the media type
    # verbatim; the parameter form ``application/problem+json;
    # charset=utf-8`` would also satisfy the SPEC intent but the
    # TEST_SPEC pins the exact token).
    result_response_content_type = response.headers["content-type"]
    assert result_response_content_type == "application/problem+json"


# ---------------------------------------------------------------------------
# Coverage tests — exercise SPEC-required branches of the FR-10 surface
# that the four canonical cases do not touch. Each test cites the source
# line(s) it covers so a future audit can drop or extend individual cases.
# ---------------------------------------------------------------------------


# NFR-04 NFR-09
def test_fr10_problem_envelope_includes_instance_field():
    """Coverage — errors.py:_problem_envelope. Direct unit-test of the
    envelope dict so the GREEN step is forced to add ``instance`` even
    if the integration test for ``test_fr10_problem_json_fields`` is
    later rewritten.
    """
    from taskq_api.errors import NotFoundProblem, _problem_envelope

    exc = NotFoundProblem(detail="envelope unit")
    cid = "00000000-0000-0000-0000-000000000000"
    envelope = _problem_envelope(exc, correlation_id=cid)

    assert "instance" in envelope
    assert len(envelope["instance"]) > 0


# NFR-02 NFR-09
def test_fr10_validation_handler_emits_problem_envelope():
    """Coverage — errors.py:_validation_exception_handler. The 422
    path (RequestValidationError) MUST serialise the same envelope
    shape as the typed ProblemDetail handler, including the
    ``instance`` field that is currently missing on the GREEN
    pending list.
    """
    from fastapi import FastAPI
    from pydantic import BaseModel

    app = FastAPI()
    register_error_handlers(app)

    class _Schema(BaseModel):
        name: str

    @app.post("/_test_fr10_validation_path")
    async def _endpoint(payload: _Schema) -> dict:
        return payload.model_dump()

    response = _run(_hit(app, "post", "/_test_fr10_validation_path", json={}))

    assert response.status_code == 422
    envelope = response.json()
    for required_field in ("type", "title", "status", "detail", "instance", "correlation_id"):
        assert required_field in envelope, f"missing envelope field {required_field!r}"
    assert envelope["type"].startswith("/errors/")
    assert envelope["status"] == 422
    assert envelope["title"] == "Validation Error"
    assert len(envelope["instance"]) > 0
    assert len(envelope["correlation_id"]) == 36


# NFR-02 NFR-04 NFR-09
def test_fr10_generic_exception_handler_sanitises_500():
    """Coverage — errors.py:_generic_exception_handler (to be added by
    GREEN). An arbitrary ``Exception`` raised inside a handler MUST
    land as a 500 ``application/problem+json`` envelope with a
    whitelisted ``detail``; the original traceback / SQL / path
    fragments MUST NOT be echoed.
    """
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/_test_fr10_generic")
    async def _boom() -> None:
        raise ValueError(
            "Traceback (most recent call last):\n"
            "  File \"/usr/local/lib/x.py\", line 1, in <module>\n"
            "    SELECT * FROM credentials"
        )

    response = _run(_hit(app, "get", "/_test_fr10_generic"))

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.text
    assert "Traceback" not in body
    assert "SELECT" not in body
    assert "/usr/" not in body

    envelope = response.json()
    assert envelope["type"].startswith("/errors/")
    assert envelope["status"] == 500
    assert len(envelope["detail"]) > 0
    # Detail must NOT carry the original exception message verbatim.
    assert "Traceback" not in envelope["detail"]
    assert "SELECT" not in envelope["detail"]
    assert "/usr/" not in envelope["detail"]
    assert len(envelope["correlation_id"]) == 36


# NFR-04 NFR-09
def test_fr10_register_error_handlers_registers_generic_exception():
    """Coverage — errors.py:register_error_handlers. The handler set
    MUST include a generic ``Exception`` handler so the sanitised
    500 path is reachable for unhandled exceptions.
    """
    app = FastAPI()
    register_error_handlers(app)

    # FastAPI exposes the registered handlers as a dict keyed by
    # exception class. ``Exception`` must be a registered key once
    # GREEN lands.
    handler_dict = app.exception_handlers
    assert Exception in handler_dict


# NFR-04 NFR-09
def test_fr10_correlation_id_round_trip_to_header():
    """Coverage — errors.py:_problem_exception_handler. Direct unit
    test of the handler confirms the body correlation_id matches the
    ``X-Correlation-Id`` header that is set on the outgoing
    ``JSONResponse``.
    """
    from starlette.requests import Request

    from taskq_api.errors import _problem_exception_handler

    # Construct a minimal Request scope so the handler can read the
    # URL path for the ``instance`` field.
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/_test_fr10_corr_roundtrip",
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)

    exc = NotFoundProblem(detail="roundtrip unit")
    response = _run(_problem_exception_handler(request, exc))

    assert response.status_code == 404
    body_bytes = response.body
    import json as _stdlib_json

    envelope = _stdlib_json.loads(body_bytes)
    cid_value = envelope["correlation_id"]
    assert len(cid_value) == 36
    # ``JSONResponse.headers`` is a mutable mapping; the handler sets
    # the lowercase form of the canonical X-Correlation-Id header.
    assert response.headers["x-correlation-id"] == cid_value


# ---------------------------------------------------------------------------
# Coverage backfill — exercise lines the FR-10 spec tests do not hit.
#
# The spec test functions only cover the envelope contract surface. The
# source modules bound by the SAB (errors.py / tasks.py / deps.py)
# contain additional branches — typed ProblemDetail subclasses, the
# rate-limit / auth gate, the task router endpoints — that need a
# reachable test or a coverage pragma to satisfy the NFR-10 line gate.
# Each test below cites the source line(s) it covers.
# ---------------------------------------------------------------------------


# NFR-09 NFR-10
def test_fr10_problem_detail_constructor_with_overrides():
    """[FR-10] Cover errors.py:42-45 — ProblemDetail accepts
    ``title`` and ``type_uri`` overrides and applies them to the
    instance attributes before the base ``Exception.__init__`` fires.

    The default construction path (all params default) is the only one
    the spec tests hit; this exercises the explicit-override branches.
    """

    custom = ProblemDetail(
        status=418,
        title="I'm a teapot",
        detail="short and stout",
        type_uri="/errors/teapot",
    )
    assert custom.status == 418
    assert custom.title == "I'm a teapot"
    assert custom.type_uri == "/errors/teapot"
    assert custom.detail == "short and stout"


# NFR-09 NFR-10
def test_fr10_auth_problem_constructor_with_detail():
    """[FR-10] Cover errors.py:75-76 — AuthProblem.__init__ delegates
    to ProblemDetail with status=401. Default-construction does not
    exercise the kwarg passthrough; this test forces the explicit
    detail branch.
    """

    exc = AuthProblem(detail="custom auth failure reason")
    assert exc.status == 401
    assert exc.type_uri == "/errors/unauthenticated"
    assert exc.title == "Unauthenticated"
    assert exc.detail == "custom auth failure reason"


# NFR-09 NFR-10
def test_fr10_forbidden_problem_constructor_with_detail():
    """[FR-10] Cover errors.py:89-90 — ForbiddenProblem.__init__
    delegates to ProblemDetail with status=403. Explicit-detail
    branch is otherwise unreached by the spec tests.
    """

    exc = ForbiddenProblem(detail="custom forbidden reason")
    assert exc.status == 403
    assert exc.type_uri == "/errors/forbidden"
    assert exc.title == "Forbidden"
    assert exc.detail == "custom forbidden reason"


# NFR-09 NFR-10
def test_fr10_conflict_problem_constructor_with_detail():
    """[FR-10] Cover errors.py:117-118 — ConflictProblem.__init__
    delegates to ProblemDetail with status=409. The duplicate-name
    path that the spec tests already cover does pass through this
    line, but a direct unit-test keeps the contract invariant
    anchored even if the route layer is refactored.
    """

    exc = ConflictProblem(detail="custom conflict reason")
    assert exc.status == 409
    assert exc.type_uri == "/errors/conflict"
    assert exc.title == "Conflict"
    assert exc.detail == "custom conflict reason"


# NFR-09 NFR-10
@pytest.mark.asyncio
async def test_fr10_sanitised_middleware_non_http_scope_passthrough():
    """[FR-10] Cover errors.py:303-306 — _SanitisedExceptionMiddleware
    passes non-HTTP scopes (e.g. ``lifespan``) through without
    invoking the exception handler. The middleware MUST NOT swallow
    lifespan or websocket scopes.
    """

    sentinel_seen = {"called": False}

    async def _inner_app(scope, receive, send):
        sentinel_seen["called"] = True

    captured: dict = {}

    async def _handler(_request, _exc):
        captured["handler"] = True
        return None  # pragma: no cover — handler must NOT be reached here

    from taskq_api.errors import _SanitisedExceptionMiddleware
    middleware = _SanitisedExceptionMiddleware(app=_inner_app, handler=_handler)

    async def _noop_receive():
        return {"type": "lifespan.startup"}

    async def _noop_send(_message):
        return None

    await middleware({"type": "lifespan"}, _noop_receive, _noop_send)
    assert sentinel_seen["called"] is True
    assert "handler" not in captured


# NFR-09 NFR-10
@pytest.mark.asyncio
async def test_fr10_sanitised_middleware_response_started_reraise():
    """[FR-10] Cover errors.py:316-322 — _SanitisedExceptionMiddleware
    re-raises exceptions raised AFTER the response has started so
    ServerErrorMiddleware can still log them. The sanitised body
    MUST NOT replace a response that is already in flight.
    """

    captured: dict = {}

    async def _inner_app(scope, receive, send):
        # Simulate a response that has already started before the
        # exception fires, then raise so the middleware's except
        # branch has to re-raise (response_started=True).
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("late failure after response started")

    async def _handler(_request, _exc):
        captured["handler_called"] = True
        return None  # pragma: no cover — must NOT be reached

    from taskq_api.errors import _SanitisedExceptionMiddleware
    middleware = _SanitisedExceptionMiddleware(app=_inner_app, handler=_handler)

    async def _noop_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    send_calls: list[dict] = []

    async def _send(message):
        send_calls.append(message)

    with pytest.raises(RuntimeError) as excinfo:
        await middleware(
            {"type": "http", "method": "GET", "path": "/x", "headers": []},
            _noop_receive,
            _send,
        )
    assert "late failure" in str(excinfo.value)
    assert "handler_called" not in captured
    # The http.response.start message MUST have been forwarded to the
    # outer send so the response stays valid through the re-raise.
    assert any(m.get("type") == "http.response.start" for m in send_calls)


# ---------------------------------------------------------------------------
# Coverage backfill — api/deps.py
# ---------------------------------------------------------------------------


# NFR-09 NFR-10
def test_fr10_deps_read_rate_config_when_env_set(monkeypatch):
    """[FR-10] Cover deps.py:73-78 — _read_rate_config returns a
    populated _RateConfig when TASKQ_RATE_BURST is set. The spec
    tests do not exercise the FR-05 surface; this anchors the
    contract for the rate-limit opt-in.
    """
    from taskq_api.api import deps as _deps

    monkeypatch.setenv("TASKQ_RATE_BURST", "5")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "0.5")
    cfg = _deps._read_rate_config()
    assert cfg is not None
    assert cfg.burst == 5
    assert cfg.rate_per_sec == 0.5


# NFR-09 NFR-10
def test_fr10_deps_enforce_rate_limit_disabled_when_config_none(monkeypatch):
    """[FR-10] Cover deps.py:95-97 — _enforce_rate_limit short-circuits
    when no rate-limit config is present (the FR-05 opt-in default).
    """
    from taskq_api.api import deps as _deps

    monkeypatch.delenv("TASKQ_RATE_BURST", raising=False)
    # Must NOT raise — the env is unset so the bucket check is skipped.
    _deps._enforce_rate_limit("any-token")


# NFR-09 NFR-10
def test_fr10_deps_enforce_rate_limit_allowed(monkeypatch):
    """[FR-10] Cover deps.py:99-107 — _enforce_rate_limit consumes a
    token and returns None when the bucket has capacity. The ``with
    _RATE_LOCK`` block + allowed branch are otherwise unreached.
    """
    from taskq_api.api import deps as _deps

    monkeypatch.setenv("TASKQ_RATE_BURST", "3")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "1.0")

    class _FakeDecision:
        allowed = True
        retry_after_seconds = 0
        tokens = 2.0

    monkeypatch.setattr(
        _deps,
        "check_and_consume",
        lambda *, token, burst, rate_per_sec: _FakeDecision(),
    )
    # Must NOT raise — the bucket has capacity.
    _deps._enforce_rate_limit("capacity-token")


# NFR-09 NFR-10
def test_fr10_deps_enforce_rate_limit_denied_raises_429(monkeypatch):
    """[FR-10] Cover deps.py:106-116 — _enforce_rate_limit raises
    HTTPException(429) with the Retry-After header when the bucket
    is exhausted.
    """
    from fastapi import HTTPException

    from taskq_api.api import deps as _deps

    monkeypatch.setenv("TASKQ_RATE_BURST", "1")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "0.5")

    class _FakeDecision:
        allowed = False
        retry_after_seconds = 2
        tokens = 0.0

    monkeypatch.setattr(
        _deps,
        "check_and_consume",
        lambda *, token, burst, rate_per_sec: _FakeDecision(),
    )
    with pytest.raises(HTTPException) as excinfo:
        _deps._enforce_rate_limit("exhausted-token")
    assert excinfo.value.status_code == 429
    assert excinfo.value.headers is not None
    assert "Retry-After" in excinfo.value.headers
    assert excinfo.value.headers["Retry-After"] == "2"


# NFR-09 NFR-10
def test_fr10_deps_get_current_key_missing_header():
    """[FR-10] Cover deps.py:137-139 — get_current_key raises
    AuthProblem when the X-API-Key header is absent.
    """
    from starlette.requests import Request

    from taskq_api.api import deps as _deps

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/x",
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)
    with pytest.raises(AuthProblem) as excinfo:
        _deps.get_current_key(request)
    assert "X-API-Key header is required" in str(excinfo.value.detail)


# NFR-09 NFR-10
def test_fr10_deps_get_current_key_invalid_key():
    """[FR-10] Cover deps.py:143-144 — get_current_key raises
    AuthProblem when ``verify_key`` returns False.
    """
    from starlette.requests import Request

    from taskq_api.api import deps as _deps
    from taskq_api.service import auth as _auth

    original_verify = _auth.verify_key
    _auth.verify_key = lambda _raw, _hashed: False
    try:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/v1/x",
            "headers": [(b"x-api-key", b"any-key")],
            "query_string": b"",
        }
        request = Request(scope)
        with pytest.raises(AuthProblem) as excinfo:
            _deps.get_current_key(request)
        assert "not valid" in str(excinfo.value.detail)
    finally:
        _auth.verify_key = original_verify


# NFR-09 NFR-10
def test_fr10_deps_get_current_key_valid_returns_raw():
    """[FR-10] Cover deps.py:145 — get_current_key returns the raw
    key when verify_key accepts it.
    """
    from starlette.requests import Request

    from taskq_api.api import deps as _deps
    from taskq_api.service import auth as _auth

    original_verify = _auth.verify_key
    _auth.verify_key = lambda raw, _hashed: True
    try:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/v1/x",
            "headers": [(b"x-api-key", b"valid-key")],
            "query_string": b"",
        }
        request = Request(scope)
        result = _deps.get_current_key(request)
        assert result == "valid-key"
    finally:
        _auth.verify_key = original_verify


# NFR-09 NFR-10
def test_fr10_deps_scope_gate_init_and_call_allowed():
    """[FR-10] Cover deps.py:166-178 — _ScopeGate.__init__ stores
    the allowed_scopes frozenset, and the callable path returns the
    key when scope_allows is True.
    """
    from taskq_api.api import deps as _deps
    from taskq_api.service import auth as _auth

    original_allows = _auth.scope_allows
    _auth.scope_allows = lambda _key, _scopes: True
    try:
        gate = _deps._ScopeGate(frozenset({"write", "admin"}))
        assert gate.allowed_scopes == frozenset({"write", "admin"})

        # The __call__ path needs a Request; build a minimal one and
        # call the gate directly with key= (the Depends default is
        # bypassed by passing key as a kwarg).
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/v1/x",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope)
        result = gate(request, key="any-key")
        assert result == "any-key"
    finally:
        _auth.scope_allows = original_allows


# NFR-09 NFR-10
def test_fr10_deps_scope_gate_call_forbidden():
    """[FR-10] Cover deps.py:176-177 — _ScopeGate.__call__ raises
    ForbiddenProblem when scope_allows returns False.
    """
    from taskq_api.api import deps as _deps
    from taskq_api.service import auth as _auth
    from starlette.requests import Request

    original_allows = _auth.scope_allows
    _auth.scope_allows = lambda _key, _scopes: False
    try:
        gate = _deps._ScopeGate(frozenset({"admin"}))
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/v1/x",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope)
        with pytest.raises(ForbiddenProblem) as excinfo:
            gate(request, key="weak-key")
        assert excinfo.value.status == 403
    finally:
        _auth.scope_allows = original_allows


# NFR-09 NFR-10
def test_fr10_deps_require_scope_returns_gate():
    """[FR-10] Cover deps.py:193 — require_scope returns a
    _ScopeGate carrying the supplied allowed_scopes.
    """
    from taskq_api.api import deps as _deps

    gate = _deps.require_scope("write", "admin")
    assert isinstance(gate, _deps._ScopeGate)
    assert gate.allowed_scopes == frozenset({"write", "admin"})


# ---------------------------------------------------------------------------
# Coverage backfill — api/tasks.py
# ---------------------------------------------------------------------------


# NFR-09 NFR-10
def test_fr10_tasks_result_from_runner_record_defaults():
    """[FR-10] Cover tasks.py:43-65 — _result_from_runner_record fills
    in defaults for omitted runner fields. The route handlers call
    this helper, but the route tests do not exercise the
    record-omits-fields branches directly.
    """
    from taskq_api.api.tasks import _result_from_runner_record
    from taskq_api.models.orm import TaskResult

    rec = _result_from_runner_record(
        task_id="t-1",
        run_id="r-1",
        record={"exit_code": 0, "stdout_tail": "ok", "stderr_tail": ""},
    )
    assert isinstance(rec, TaskResult)
    assert rec.task_id == "t-1"
    assert rec.run_id == "r-1"
    assert rec.exit_code == 0
    assert rec.stdout_tail == "ok"
    assert rec.status == "done"  # default


# NFR-09 NFR-10
def test_fr10_tasks_create_router_endpoints_exercised(monkeypatch):
    """[FR-10] Cover tasks.py:71-215 — exercise the full router
    surface so every branch (create / get / list / delete / run /
    list-runs) is reached. The FR-10 spec tests mount their own
    minimal apps, so this is the only test that drives the actual
    tasks router.

    Stubs ``repository.session.get_session`` with a no-op fake so
    TaskRepo never hits the Phase-4 deployment wiring error. The
    list endpoint reads from the in-process TaskRepo registry via
    the SQLAlchemy expression, so the fake materialises the rows
    from the registry on every execute() call.
    """
    from fastapi import FastAPI

    from taskq_api.repository import session as _session_mod
    from taskq_api.repository.task_repo import TaskRepo

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

        def execute(self, *_args, **_kwargs):
            registry_rows = list(TaskRepo._registry.values())

            class _Result:
                def __init__(self, rows):
                    self._rows = rows

                def scalars(self):
                    return self

                def unique(self):
                    return self

                def all(self):
                    return list(self._rows)

            return _Result(registry_rows)

    monkeypatch.setattr(_session_mod, "get_session", lambda: _FakeSession())

    TaskRepo._registry.clear()
    TaskRepo._by_name.clear()

    app = FastAPI()
    app.include_router(create_tasks_router())

    # POST /v1/tasks — happy path (line 99: service.create).
    create_response = _run(
        _hit(
            app,
            "post",
            "/v1/tasks",
            json={"name": "fr10-coverage", "command": "echo hello"},
            headers={"X-API-Key": "any"},
        )
    )
    assert create_response.status_code == 201
    created = create_response.json()
    task_id = created["id"]
    assert task_id
    assert created["name"] == "fr10-coverage"

    # GET /v1/tasks/{id} — line 114.
    get_response = _run(
        _hit(app, "get", f"/v1/tasks/{task_id}", headers={"X-API-Key": "any"})
    )
    assert get_response.status_code == 200
    assert get_response.json()["id"] == task_id

    # GET /v1/tasks — list (lines 134-139).
    list_response = _run(
        _hit(app, "get", "/v1/tasks", headers={"X-API-Key": "any"})
    )
    assert list_response.status_code == 200
    body = list_response.json()
    assert body["limit"] == 50
    assert any(item["id"] == task_id for item in body["items"])

    # DELETE /v1/tasks/{id} — line 154.
    delete_response = _run(
        _hit(app, "delete", f"/v1/tasks/{task_id}", headers={"X-API-Key": "any"})
    )
    assert delete_response.status_code == 204


# NFR-09 NFR-10
def test_fr10_tasks_create_rejects_shell_metacharacters(monkeypatch):
    """[FR-10] Cover tasks.py:97-98 — POST /v1/tasks rejects shell
    metacharacters in the command. ValidationProblem → 422 envelope.
    """
    from fastapi import FastAPI

    from taskq_api.repository import session as _session_mod
    from taskq_api.repository.task_repo import TaskRepo

    class _FakeSession:
        def add(self, _row):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

        def execute(self, *_args, **_kwargs):
            class _Result:
                def scalars(self):
                    return self

                def unique(self):
                    return self

                def all(self):
                    return []

            return _Result()

    monkeypatch.setattr(_session_mod, "get_session", lambda: _FakeSession())

    TaskRepo._registry.clear()
    TaskRepo._by_name.clear()

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_tasks_router())

    response = _run(
        _hit(
            app,
            "post",
            "/v1/tasks",
            json={"name": "fr10-injection", "command": "echo `rm -rf /`"},
            headers={"X-API-Key": "any"},
        )
    )
    assert response.status_code == 422
    body = response.json()
    assert "forbidden" in body["detail"].lower()


# NFR-09 NFR-10
def test_fr10_tasks_duplicate_create_returns_409(monkeypatch):
    """[FR-10] Cover tasks.py → service.tasks:43-44 — duplicate
    task name returns 409 ConflictProblem.
    """
    from fastapi import FastAPI

    from taskq_api.repository import session as _session_mod
    from taskq_api.repository.task_repo import TaskRepo

    class _FakeSession:
        def add(self, _row):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

        def execute(self, *_args, **_kwargs):
            class _Result:
                def scalars(self):
                    return self

                def unique(self):
                    return self

                def all(self):
                    return []

            return _Result()

    monkeypatch.setattr(_session_mod, "get_session", lambda: _FakeSession())

    TaskRepo._registry.clear()
    TaskRepo._by_name.clear()

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_tasks_router())

    headers = {"X-API-Key": "any"}
    first = _run(
        _hit(
            app,
            "post",
            "/v1/tasks",
            json={"name": "dup-coverage", "command": "echo a"},
            headers=headers,
        )
    )
    assert first.status_code == 201

    second = _run(
        _hit(
            app,
            "post",
            "/v1/tasks",
            json={"name": "dup-coverage", "command": "echo b"},
            headers=headers,
        )
    )
    assert second.status_code == 409
    body = second.json()
    assert body["type"] == "/errors/conflict"
    assert "dup-coverage" in body["detail"]


# NFR-09 NFR-10
def test_fr10_tasks_get_unknown_returns_404():
    """[FR-10] Cover tasks.py:114 → service.tasks:71-72 — GET on a
    unknown task id returns 404 NotFoundProblem.
    """
    from fastapi import FastAPI


    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_tasks_router())

    response = _run(
        _hit(
            app,
            "get",
            "/v1/tasks/00000000-0000-0000-0000-000000000000",
            headers={"X-API-Key": "any"},
        )
    )
    assert response.status_code == 404
    body = response.json()
    assert body["type"] == "/errors/not-found"


# NFR-09 NFR-10
def test_fr10_tasks_delete_unknown_returns_404(monkeypatch):
    """[FR-10] Cover tasks.py:154 → service.delete:61-62 — DELETE on
    an unknown task id returns 404 NotFoundProblem.
    """
    from fastapi import FastAPI

    from taskq_api.repository import session as _session_mod

    class _FakeSession:
        def add(self, _row):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

        def execute(self, *_args, **_kwargs):
            class _Result:
                def scalars(self):
                    return self

                def unique(self):
                    return self

                def all(self):
                    return []

            return _Result()

    monkeypatch.setattr(_session_mod, "get_session", lambda: _FakeSession())

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_tasks_router())

    response = _run(
        _hit(
            app,
            "delete",
            "/v1/tasks/00000000-0000-0000-0000-000000000000",
            headers={"X-API-Key": "any"},
        )
    )
    assert response.status_code == 404
    body = response.json()
    assert body["type"] == "/errors/not-found"


# NFR-09 NFR-10
def test_fr10_tasks_list_with_status_filter(monkeypatch):
    """[FR-10] Cover tasks.py:128-139 — list with an explicit status
    filter. Exercises the ``status`` parameter branch of
    ``service.list``.
    """
    from fastapi import FastAPI

    from taskq_api.repository import session as _session_mod
    from taskq_api.repository.task_repo import TaskRepo

    class _FakeSession:
        def add(self, _row):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

        def execute(self, *_args, **_kwargs):
            class _Result:
                def scalars(self):
                    return self

                def unique(self):
                    return self

                def all(self):
                    return []

            return _Result()

    monkeypatch.setattr(_session_mod, "get_session", lambda: _FakeSession())

    TaskRepo._registry.clear()
    TaskRepo._by_name.clear()

    app = FastAPI()
    app.include_router(create_tasks_router())

    # Seed one task so the list is non-empty.
    create = _run(
        _hit(
            app,
            "post",
            "/v1/tasks",
            json={"name": "fr10-list-filter", "command": "echo hi"},
            headers={"X-API-Key": "any"},
        )
    )
    assert create.status_code == 201

    list_response = _run(
        _hit(
            app,
            "get",
            "/v1/tasks",
            params={"status": "pending", "limit": "5"},
            headers={"X-API-Key": "any"},
        )
    )
    assert list_response.status_code == 200
    body = list_response.json()
    assert body["limit"] == 5
    assert isinstance(body["items"], list)


# NFR-09 NFR-10
def test_fr10_tasks_run_then_list_runs(monkeypatch):
    """[FR-10] Cover tasks.py:170-180 (run_task) + 193-213
    (list_runs). The runner actually executes ``echo hello`` via
    asyncio subprocess and the result row is appended to the
    in-process TaskResult registry.
    """
    from fastapi import FastAPI

    from taskq_api.models.orm import TaskResult
    from taskq_api.repository import session as _session_mod
    from taskq_api.repository.task_repo import TaskRepo

    class _FakeSession:
        def add(self, _row):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

        def execute(self, *_args, **_kwargs):
            class _Result:
                def scalars(self):
                    return self

                def unique(self):
                    return self

                def all(self):
                    return []

            return _Result()

    monkeypatch.setattr(_session_mod, "get_session", lambda: _FakeSession())

    TaskRepo._registry.clear()
    TaskRepo._by_name.clear()
    TaskResult._registry.clear()

    app = FastAPI()
    app.include_router(create_tasks_router())

    create = _run(
        _hit(
            app,
            "post",
            "/v1/tasks",
            json={"name": "fr10-run-coverage", "command": "echo hello"},
            headers={"X-API-Key": "any"},
        )
    )
    assert create.status_code == 201
    task_id = create.json()["id"]

    run_response = _run(
        _hit(app, "post", f"/v1/tasks/{task_id}/run", headers={"X-API-Key": "any"})
    )
    assert run_response.status_code == 202
    run_body = run_response.json()
    assert run_body["run_id"]
    assert run_body["status"] == "done"

    list_runs_response = _run(
        _hit(app, "get", f"/v1/tasks/{task_id}/runs", headers={"X-API-Key": "any"})
    )
    assert list_runs_response.status_code == 200
    runs_body = list_runs_response.json()
    assert len(runs_body["items"]) == 1
    assert runs_body["items"][0]["run_id"] == run_body["run_id"]


# NFR-09 NFR-10
def test_fr10_tasks_run_unknown_task_returns_404():
    """[FR-10] Cover tasks.py:174-175 — POST /v1/tasks/{id}/run on a
    unknown task returns 404.
    """
    from fastapi import FastAPI


    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_tasks_router())

    response = _run(
        _hit(
            app,
            "post",
            "/v1/tasks/00000000-0000-0000-0000-000000000000/run",
            headers={"X-API-Key": "any"},
        )
    )
    assert response.status_code == 404
    assert response.json()["type"] == "/errors/not-found"


# NFR-09 NFR-10
def test_fr10_tasks_list_runs_unknown_task_returns_404():
    """[FR-10] Cover tasks.py:197-198 — GET /v1/tasks/{id}/runs on a
    unknown task returns 404.
    """
    from fastapi import FastAPI


    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_tasks_router())

    response = _run(
        _hit(
            app,
            "get",
            "/v1/tasks/00000000-0000-0000-0000-000000000000/runs",
            headers={"X-API-Key": "any"},
        )
    )
    assert response.status_code == 404
    assert response.json()["type"] == "/errors/not-found"