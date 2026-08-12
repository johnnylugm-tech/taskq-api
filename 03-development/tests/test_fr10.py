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

    The ``asyncio.get_event_loop().run_until_complete`` pattern matches
    the FR-09 test suite — keeps the test functions synchronous so the
    pytest runner surfaces RED failures as plain assertion errors rather
    than coroutine warnings.
    """
    return asyncio.get_event_loop().run_until_complete(coro)


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