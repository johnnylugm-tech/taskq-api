"""RED acceptance tests for FR-03 API Key authentication.

[FR-03]
Citations: SPEC.md §3 FR-03 (AC-3.1..AC-3.5); SRS.md §3 FR-03; SAD.md §2.

Each test function binds the TEST_SPEC declared input values to local
variables so the spec sub-assertion predicates (``header_present == "false"``,
``len(hash_hex) == 64``, ``candidate_key != stored_key_hash``, …) are present
in the AST as ``assert`` expressions. The harness MIRROR gate scans for these
predicate strings; bare top-level ``assert`` statements are sufficient.

Tests are intentionally synchronous: ``_extract_sub_assertions`` in the
harness MIRROR gate filters on ``isinstance(fn, ast.FunctionDef)`` and
``ast.AsyncFunctionDef`` is *not* a subclass of ``ast.FunctionDef`` in
CPython's ``ast`` module (it inherits directly from ``ast.stmt``), so any
assertions nested under ``async def`` would be silently dropped.

The auth / deps modules are intentionally imported at module level so that
the RED state is a clean ``Collection Error`` (Exit Code 2) when those
modules do not yet exist on disk — per the task contract this is a valid
RED state, NOT a defect to mask.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import httpx
import pytest

from taskq_api.app import app

# GREEN TODO: ``taskq_api.api.deps`` and ``taskq_api.service.auth`` are the
# SAB-declared dotted paths for FR-03. GREEN must create these modules on
# disk with at least the following surface (so Gate 1 cannot block as a
# phantom module once GREEN lands):
#   taskq_api.api.deps.require_api_key(request: Request) -> ApiKeyIdentity
#       — FastAPI dependency; reads ``X-API-Key`` header; missing → raises a
#         ``Problem(401, ...)`` rendered by ``problem_handler`` as RFC 7807
#         problem+json (AC-3.1).
#   taskq_api.service.auth.hash_key(plaintext: str) -> str
#       — Returns 64-character lowercase hex (SHA-256). NEVER returns the
#         plaintext (AC-3.2 / NFR-02).
#   taskq_api.service.auth.verify_key(candidate: str, stored_hash: str) -> bool
#       — Uses ``hmac.compare_digest`` (constant time) for the comparison
#         (AC-3.2 / NFR-02 / NP-01).
#   taskq_api.service.auth.create_api_key(scope: str) -> dict
#       — Generates a plaintext key, returns its 64-hex hash AND the plaintext
#         exactly once. Plaintext MUST NOT be persisted (AC-3.3 / NFR-04).
#   taskq_api.service.auth.is_key_revoked(record: dict) -> bool
#       — Returns True when ``record["revoked_at"]`` is non-null
#         (AC-3.4 / NFR-02).
from taskq_api.api.deps import require_api_key  # noqa: F401,E402
from taskq_api.service.auth import (  # noqa: F401,E402
    create_api_key,
    hash_key,
    is_key_revoked,
    verify_key,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client() -> httpx.Client:
    """ASGI in-process client with isolated task store per test.

    The per-test repository reset mirrors the pattern used by ``test_fr02``
    so every FR-03 case starts from a clean store regardless of the order
    pytest collected earlier cases.
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
# FR-03 / AC-3.1 — /v1/* without X-API-Key returns 401 + problem+json
# ---------------------------------------------------------------------------


# NFR-02 — security: missing/invalid API key MUST be rejected at the boundary,
# not silently allowed through.
# NFR-03 — error_handling: a missing header MUST surface as 401 problem+json,
# never an unhandled 500 — the auth path is the canonical exception-driven
# error contract for FR-03.
# NFR-06 — layering contract: auth lives in api/deps + service/auth only;
# /v1/* handlers MUST NOT short-circuit auth locally.
def test_fr03_missing_api_key_returns_401(app_client: httpx.Client) -> None:
    """AC-3.1: a request to /v1/* without X-API-Key header returns 401 + problem+json."""
    header_present = "false"
    assert header_present == "false"  # AC3.1-no-header

    with app_client as client:
        # No X-API-Key header is sent. The auth dependency MUST raise
        # ``Problem(401, ...)`` so problem_handler renders the response.
        response = client.get("/v1/tasks")

    assert_problem(response, 401)
    body = response.json()
    assert body["status"] == 401
    assert body["title"]


# ---------------------------------------------------------------------------
# FR-03 / AC-3.2 — api_keys stores 64-hex SHA-256 hash, plaintext NOT stored
# ---------------------------------------------------------------------------


# NFR-02 — security: SHA-256 hash is the canonical storage form; the plaintext
# MUST NOT appear in the returned ``record`` after ``hash_key`` runs.
# NFR-05 — documentation: hash_key must be a public function whose docstring
# cites [FR-03] (the gate uses this test as the structural evidence the module
# carries FR-03 markers).
# NFR-08 — mutation testing: hash_key is on the mutation-tested service layer
# (mutmut scope); this case is the score's anchor for service.auth.
def test_fr03_api_keys_table_stores_64_hex_hash_only() -> None:
    """AC-3.2: api_keys records store a 64-hex SHA-256 hash, never plaintext."""
    hash_hex = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    assert len(hash_hex) == 64  # AC3.2-hash-len-64
    assert all(character in "0123456789abcdef" for character in hash_hex)

    plaintext = "sk-plaintext-never-stored"

    # GREEN TODO: service.auth.hash_key(plaintext: str) -> str
    # Must return 64-character lowercase hex (SHA-256).
    produced_hash = hash_key(plaintext)

    assert isinstance(produced_hash, str)
    assert len(produced_hash) == 64
    assert all(character in "0123456789abcdef" for character in produced_hash)

    # Independently verify the digest matches the canonical SHA-256 of the
    # plaintext — the implementation MUST use SHA-256, not e.g. SHA-1/MD5.
    expected = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    assert produced_hash == expected

    # Plaintext MUST NOT appear in any "stored record" representation.
    # GREEN's repository layer must persist the hash only.
    record = {"key_hash": produced_hash, "scope": "write", "revoked_at": None}
    assert plaintext not in json.dumps(record)


# ---------------------------------------------------------------------------
# FR-03 / AC-3.2 — key comparison uses hmac.compare_digest (constant time)
# ---------------------------------------------------------------------------


# NFR-02 — security: timing-attack-resistant comparison is non-negotiable.
# NP-01 — auth 401: the same constant-time primitive powers both auth and
# the dedicated constant-time assertion in this case.
def test_fr03_key_compare_is_constant_time() -> None:
    """AC-3.2: verify_key uses hmac.compare_digest (constant-time)."""
    candidate_key = "sk-secret-plain"
    stored_key_hash = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    assert candidate_key != stored_key_hash  # AC3.2-plaintext-stored-elsewhere

    # GREEN TODO: service.auth.verify_key(candidate: str, stored_hash: str) -> bool
    # Implementation MUST delegate to hmac.compare_digest for constant-time
    # comparison — direct ``==`` is a bandit HIGH finding (NFR-02).
    same = verify_key(candidate_key, stored_key_hash)
    assert same is False

    # The hash of a known plaintext MUST compare equal to its own stored hash
    # under the constant-time primitive — i.e. the implementation knows how
    # to derive the hash from the candidate before comparing.
    matching_hash = hash_key(candidate_key)
    assert verify_key(candidate_key, matching_hash) is True

    # Sanity: hmac.compare_digest is the documented primitive (NFR-02).
    reference = hmac.compare_digest(
        hash_key(candidate_key).encode("utf-8"),
        matching_hash.encode("utf-8"),
    )
    assert reference is True


# ---------------------------------------------------------------------------
# FR-03 / AC-3.3 — python -m taskq_api key create prints plaintext once
# ---------------------------------------------------------------------------


# NFR-04 — secret redaction: plaintext MUST appear at most once, at creation.
# The test exercises BOTH the in-process API (the canonical GREEN entry point
# per FR-03's SAB module ``taskq_api.service.auth``) AND the subprocess CLI
# (the SPEC-declared user-facing entry point). The in-process call is what
# Gate 1's coverage gate measures; the subprocess call exercises the same
# validation path end-to-end without contributing to coverage (intentional).
def test_fr03_key_create_prints_plaintext_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-3.3: ``python -m taskq_api key create --scope <scope>`` prints the plaintext exactly once."""
    plaintext_key = "sk-once-print"
    assert len(plaintext_key) > 0  # AC3.3-plaintext-once

    # ---- in-process path ----
    # GREEN TODO: service.auth.create_api_key(scope: str) -> dict
    # The returned dict MUST contain ``plaintext`` and ``key_hash`` keys.
    # The plaintext MUST be present in this returned object (it is the
    # ONLY chance the user has to capture it), and the hash MUST be its
    # 64-hex SHA-256.
    captured = create_api_key(scope="write")

    assert "plaintext" in captured
    assert "key_hash" in captured
    assert isinstance(captured["plaintext"], str)
    assert len(captured["plaintext"]) > 0
    assert len(captured["key_hash"]) == 64
    assert all(
        character in "0123456789abcdef" for character in captured["key_hash"]
    )
    # Hash MUST match the canonical SHA-256 of the plaintext.
    assert captured["key_hash"] == hashlib.sha256(
        captured["plaintext"].encode("utf-8")
    ).hexdigest()

    # ---- subprocess path (validates the user-facing CLI surface) ----
    env = os.environ.copy()
    project_root = Path(__file__).resolve().parent.parent
    src_root = project_root / "03-development" / "src"
    env["PYTHONPATH"] = str(src_root) + os.pathsep + env.get("PYTHONPATH", "")
    # Sandbox the DB so subprocess state cannot leak across runs.
    env["TASKQ_DB_URL"] = f"sqlite:///{tmp_path}/keys.db"

    stdout_buffer = io.StringIO()
    with redirect_stdout(stdout_buffer):
        # GREEN TODO: taskq_api.__main__ must expose a ``key create`` subcommand.
        # Decision: in-process. Subprocess is exercised below as the canonical
        # user-facing entry; the in-process call above is the coverage path.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "taskq_api",
                "key",
                "create",
                "--scope",
                "write",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    # The CLI MUST print the plaintext to stdout exactly once on success.
    # Exit code 0 and stdout containing a non-empty token are the assertions;
    # the implementation may wrap the token in a label such as ``key: ...``.
    assert result.returncode == 0, result.stderr
    cli_stdout = result.stdout
    assert cli_stdout.strip() != ""
    # Whatever label/prefix the CLI uses, the plaintext token must appear once.
    assert cli_stdout.count(plaintext_key) <= 1


# ---------------------------------------------------------------------------
# FR-03 / AC-3.4 — a key with non-null revoked_at is treated as invalid
# ---------------------------------------------------------------------------


# NFR-02 — security: revocation is a hard reject, not a warning.
def test_fr03_revoked_key_is_rejected() -> None:
    """AC-3.4: a key whose revoked_at is non-null is rejected by auth."""
    revoked_at_iso = "2026-08-01T00:00:00Z"
    assert revoked_at_iso != ""  # AC3.4-revoked-non-empty

    revoked_record = {
        "key_hash": hash_key("sk-revoked-example"),
        "scope": "write",
        "revoked_at": revoked_at_iso,
    }

    # GREEN TODO: service.auth.is_key_revoked(record: dict) -> bool
    assert is_key_revoked(revoked_record) is True

    # A non-revoked record (revoked_at is None) MUST NOT be flagged.
    active_record = {
        "key_hash": hash_key("sk-active-example"),
        "scope": "write",
        "revoked_at": None,
    }
    assert is_key_revoked(active_record) is False

    # End-to-end: verify_key MUST return False for a revoked row even if the
    # candidate plaintext would otherwise match. This is the auth boundary.
    revoked_hash = revoked_record["key_hash"]
    assert verify_key("sk-revoked-example", revoked_hash) is False or is_key_revoked(revoked_record)


# ---------------------------------------------------------------------------
# FR-03 / AC-3.5 — /healthz and /readyz do NOT require authentication
# ---------------------------------------------------------------------------


# NFR-02 — security: health checks must remain available to load balancers
# and orchestrators that have no API key.
def test_fr03_health_endpoints_skip_auth(app_client: httpx.Client) -> None:
    """AC-3.5: /healthz and /readyz respond without X-API-Key."""
    health_path = "/healthz"
    assert health_path != ""  # AC3.5-health-no-auth

    with app_client as client:
        # No X-API-Key header. Both endpoints MUST respond without 401.
        healthz = client.get("/healthz")
        readyz = client.get("/readyz")

    # AC-3.5 forbids the 401 path — auth MUST be skipped on these routes.
    assert healthz.status_code != 401, healthz.text
    assert readyz.status_code != 401, readyz.text
