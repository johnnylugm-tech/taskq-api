"""TDD-RED failing tests for FR-03 (API Key 認證).

These tests intentionally fail because the source modules declared in
the SAB for FR-03 do not yet exist on disk:

    taskq_api.api.deps             (auth dependency wiring; [FR-03][FR-04])
    taskq_api.repository.key_repo  (api_keys aggregate repository; [FR-03])
    taskq_api.models.orm.ApiKey    (api_keys ORM row; [FR-03])

The GREEN step will implement them; this RED step locks the contract.

Per TEST_SPEC.md (FR-03), the six test functions below cover the
canonical acceptance criteria:

    AC1-missing-key             POST /v1/tasks (no X-API-Key) -> 401
    AC2-no-plaintext-column     api_keys ORM row has no plaintext column
    AC2-hash-len-64             key_hash is exactly 64 hex chars
    AC2-hash-hex-chars          key_hash uses lower-case hex only
    AC3-uses-compare-digest     service/auth.py uses hmac.compare_digest
    AC4-plaintext-once          key create CLI prints plaintext exactly once
    AC4-plaintext-not-persisted api_keys has no plaintext column
    AC5-redacted-db-url         logs contain no DB URL password fragment
    AC5-redacted-metrics        /v1/metrics contains no DB URL password fragment
    AC6-revoked-status          revoked_at non-null -> 401

The TEST_SPEC names are the contract; spec-coverage-check uses exact
match. Do NOT rename these functions.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# Top-level imports — RED will surface as ModuleNotFoundError for
# `taskq_api.api.deps` and `taskq_api.repository.key_repo`, and as
# ImportError for `taskq_api.models.orm.ApiKey`. It is EXPECTED and
# acceptable for pytest to fail with Collection Error (Exit Code 2)
# at this stage.
from taskq_api.api.deps import get_current_key, require_scope  # noqa: F401
from taskq_api.app import create_app
from taskq_api.models.orm import ApiKey  # noqa: F401
from taskq_api.repository.key_repo import KeyRepo  # noqa: F401
from taskq_api.service.auth import verify_key  # noqa: F401


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def write_api_key() -> str:
    """Static write-scoped key used by FR-03 happy-path tests."""
    return "test-write-key"


@pytest.fixture(autouse=True)
def _stub_external_side_effects(monkeypatch):
    """Stub external side-effects so tests fail for FEATURE reasons only.

    The autouse fixture patches the auth verifier and DB session
    acquisition so a missing feature surfaces as an assertion failure
    rather than a real DB / cryptography error. GREEN replaces the
    stubs with real HMAC + revocation + SQLAlchemy wiring.
    """
    # GREEN TODO: taskq_api.service.auth.verify_key(raw, hashed) -> bool
    # The stub accepts any non-empty pair so auth short-circuits to the
    # route. The GREEN agent must replace the stub with a real HMAC
    # constant-time comparison that also checks the `revoked_at` column
    # in the `api_keys` table (FR-03 AC6-revoked-status).
    from taskq_api.service import auth as _auth

    def _stub_verify(raw: str, hashed: str) -> bool:
        return bool(raw) and bool(hashed)

    monkeypatch.setattr(_auth, "verify_key", _stub_verify)

    # GREEN TODO: taskq_api.repository.session.get_session() ->
    # sqlalchemy.orm.Session. The stub returns a per-call fake so the
    # route exercises service validation logic without a real DB.
    from taskq_api.repository import session as _session

    class _FakeSession:
        def __init__(self):
            self._rows: list[dict] = []
            self.committed = False
            self.rolled_back = False

        def add(self, row):
            self._rows.append(row)

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            pass

        def query(self, *_args, **_kwargs):
            return self

        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            try:
                from taskq_api.repository.task_repo import TaskRepo
                rows = list(TaskRepo._registry.values())
                if rows:
                    return list(rows)
            except Exception:
                pass
            return list(self._rows)

        def first(self):
            return self._rows[0] if self._rows else None

    monkeypatch.setattr(
        _session,
        "get_session",
        lambda: _FakeSession(),
    )

    # Reset the KeyRepo in-process registry between tests so revoked /
    # plaintext assertions start from a clean slate.
    try:
        KeyRepo._registry.clear()
        KeyRepo._by_key.clear()
    except AttributeError:
        # Module is missing — RED; nothing to reset.
        pass


@pytest.fixture
async def client():
    """Yield an httpx AsyncClient bound to the FastAPI ASGI app.

    Uses ASGITransport so the request never leaves the process — the
    SUBPROCESS COVERAGE CEILING rule is N/A here because pytest-cov
    measures code executed by ASGITransport. The DB session and auth
    verifier are stubbed via the autouse fixture so no real disk I/O or
    HMAC verification occurs.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _plaintext_token_regex() -> re.Pattern[str]:
    """Match a single plaintext token printed by `key create`.

    GREEN TODO: `python -m taskq_api key create --scope write` MUST
    emit a single plaintext line in the format `KEY=<token>`. The
    regex captures the token after `KEY=`.
    """
    return re.compile(r"\bKEY=([A-Za-z0-9_\-]{16,})\b")


# ---------------------------------------------------------------------------
# FR-03 — Test cases from TEST_SPEC.md (do NOT rename)
# ---------------------------------------------------------------------------


# NFR-01 NFR-09 SEC T-02
@pytest.mark.asyncio
async def test_fr03_missing_or_invalid_key_401(client):
    """AC1-missing-key. [FR-03][NFR-01][NFR-09][SEC T-02]

    POST /v1/tasks without `X-API-Key` returns 401 + problem+json.
    Q2 / NP-01.

    GREEN TODO: `taskq_api.api.deps.get_current_key` must raise
    `AuthProblem` (status=401) when the header is missing or empty.
    """
    response = await client.post(
        "/v1/tasks",
        json={"name": "beta-build", "command": "echo beta"},
        # NOTE: no X-API-Key header on purpose — this is the RED trigger
    )

    result_status_code = response.status_code
    assert result_status_code == 401
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )


# NFR-02 NFR-06 SEC T-02
def test_fr03_api_keys_no_plaintext_hash_64hex():
    """AC2-no-plaintext-column / AC2-hash-len-64 / AC2-hash-hex-chars.
    [FR-03][NFR-02][NFR-06][SEC T-02]

    The `api_keys` ORM row exposes ONLY `key_hash` (64 lowercase hex
    chars). No plaintext column is exposed. Q6 / NP-08.

    GREEN TODO: `taskq_api.models.orm.ApiKey` must expose
    `key_hash: str` (64 hex chars) and MUST NOT expose `plaintext`,
    `secret`, `raw_key`, or any attribute that holds the original
    key string.
    """
    # AC2-no-plaintext-column: no plaintext-like attribute on the ORM.
    forbidden_attrs = ("plaintext", "secret", "raw_key", "key")
    exposed = {a for a in dir(ApiKey) if not a.startswith("_")}
    result_db_plaintext_column = next(
        (
            attr
            for attr in forbidden_attrs
            if attr in exposed and attr != "key_hash"
        ),
        None,
    )
    assert result_db_plaintext_column is None, (
        f"plaintext-like attribute exposed on ApiKey: "
        f"{result_db_plaintext_column}"
    )

    # AC2-hash-len-64 / AC2-hash-hex-chars: key_hash must be exactly
    # 64 lowercase hex characters.
    sample_hash = "0" * 64
    sample = ApiKey(scope="write", key_hash=sample_hash)
    result_key_hash_hex = sample.key_hash
    result_key_hash_hex_str = sample.key_hash
    assert len(result_key_hash_hex) == 64
    assert result_key_hash_hex_str == result_key_hash_hex_str.lower()
    assert _HEX64_RE.match(result_key_hash_hex_str), result_key_hash_hex_str


# NFR-02 SEC T-02
def test_fr03_constant_time_compare():
    """AC3-uses-compare-digest. [FR-03][NFR-02][SEC T-02]

    `taskq_api/service/auth.py` MUST use `hmac.compare_digest` for
    key comparison (constant-time; SPEC §3 FR-03 / §8 #18). Q6 /
    NP-08.

    GREEN TODO: `taskq_api.service.auth.verify_key` must call
    `hmac.compare_digest` rather than `==` (timing-attack resistant).
    """
    auth_path = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "taskq_api"
        / "service"
        / "auth.py"
    )
    result_source_text = auth_path.read_text(encoding="utf-8")
    assert "hmac.compare_digest" in result_source_text, (
        f"hmac.compare_digest not found in {auth_path}; auth.py must "
        f"use constant-time comparison (FR-03 / SPEC §8 #18)"
    )


# NFR-02 SEC T-11
def test_fr03_plaintext_only_at_creation(monkeypatch):
    """AC4-plaintext-once / AC4-plaintext-not-persisted.
    [FR-03][NFR-02][NFR-04][SEC T-11]

    `python -m taskq_api key create --scope write` prints the
    plaintext key EXACTLY ONCE on stdout. The api_keys table MUST NOT
    store the plaintext. Q6 / NP-08.

    GREEN TODO: GREEN agent must add a CLI sub-command
    `python -m taskq_api key create --scope <scope>` that:
      1. Generates a random plaintext token.
      2. Computes `key_hash = sha256(plaintext)`.
      3. Persists `(scope, key_hash)` into the api_keys table.
      4. Prints `KEY=<plaintext>` exactly once on stdout.
    The plaintext is NOT stored — only the hash.

    In-process vs out-of-process: SUBPROCESS. We invoke the CLI as a
    child process so the test mirrors the canonical user flow. The
    `pythonpath = ...` in setup.cfg does NOT propagate to children, so
    we set `PYTHONPATH` explicitly.
    """
    db_url = "sqlite:///:memory:"
    monkeypatch.setenv("TASKQ_DB_URL", db_url)

    src_root = Path(__file__).resolve().parent.parent / "src"
    env = os.environ.copy()
    env["TASKQ_DB_URL"] = db_url
    env["PYTHONPATH"] = str(src_root) + os.pathsep + env.get(
        "PYTHONPATH", ""
    )

    proc = subprocess.run(
        [sys.executable, "-m", "taskq_api", "key", "create", "--scope", "write"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    # AC4-plaintext-once: stdout must contain exactly one `KEY=<token>`
    # line. If the CLI is not implemented yet (RED), stdout is empty
    # and the regex match fails — that is the expected RED failure.
    matches = _plaintext_token_regex().findall(proc.stdout)
    assert matches, (
        f"no `KEY=<token>` line in stdout; cli_args=key create --scope write; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    plaintext = matches[0]
    result_plaintext_print_count = sum(
        1
        for token in matches
        if token == plaintext
    )
    assert result_plaintext_print_count == 1, (
        f"plaintext appeared {result_plaintext_print_count} times in "
        f"stdout; expected exactly 1 (printed only at creation)"
    )

    # AC4-plaintext-not-persisted: api_keys ORM has no plaintext column.
    forbidden_attrs = ("plaintext", "secret", "raw_key")
    exposed = {a for a in dir(ApiKey) if not a.startswith("_")}
    result_db_plaintext_column = next(
        (attr for attr in forbidden_attrs if attr in exposed), None
    )
    assert result_db_plaintext_column is None, (
        f"plaintext column exposed on api_keys: {result_db_plaintext_column}"
    )


# NFR-04 SEC T-05
@pytest.mark.asyncio
async def test_fr03_logs_metrics_no_password(monkeypatch, caplog):
    """AC5-redacted-db-url / AC5-redacted-metrics.
    [FR-03][NFR-04][SEC T-05]

    Logs and the `/v1/metrics` endpoint MUST NOT contain the password
    fragment of `TASKQ_DB_URL`. Q6 / NP-08.

    GREEN TODO: GREEN agent must:
      1. Install a logging filter (in `taskq_api.service.auth` or
         `taskq_api.repository.session`) that redacts the password
         fragment of any DB URL before it reaches a log handler.
      2. Implement `GET /v1/metrics` (FR-09) without leaking the
         configured DB URL.
    """
    db_url_value = "postgres://u:p@host:5432/db"
    monkeypatch.setenv("TASKQ_DB_URL", db_url_value)

    # Emit a log record that contains the raw DB URL; the RED surface
    # is the unredacted password fragment showing up in caplog. GREEN
    # installs a logging filter that scrubs the password.
    import logging

    logger = logging.getLogger("taskq_api.db")
    with caplog.at_level("INFO", logger="taskq_api.db"):
        logger.info("connecting to %s", db_url_value)
        # Exercise the metrics endpoint so any leak through the body
        # is caught too.
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/metrics")

    result_logs_text = caplog.text
    result_metrics_text = response.text if response is not None else ""

    assert "p@host" not in result_logs_text, (
        f"DB URL password leaked into logs: {result_logs_text!r}"
    )
    assert "p@host" not in result_metrics_text, (
        f"DB URL password leaked into /v1/metrics: {result_metrics_text!r}"
    )


# NFR-01 NFR-09 SEC T-02
@pytest.mark.asyncio
async def test_fr03_revoked_key_rejected_401(client, monkeypatch):
    """AC6-revoked-status. [FR-03][NFR-01][NFR-09][SEC T-02]

    A key whose `revoked_at` is non-null MUST be rejected with 401.
    Q2 / NP-01.

    GREEN TODO: `taskq_api.service.auth.verify_key` MUST consult the
    `api_keys` row for the supplied key and reject any key whose
    `revoked_at` is non-null. The autouse stub above accepts any
    non-empty pair; GREEN replaces it with a real lookup that checks
    `revoked_at` (FR-03 AC6 / SPEC §3 FR-03).
    """
    revoked_at = "2026-01-01T00:00:00Z"
    key_value = "stale-key"

    # Pre-populate the in-process api_keys registry with a revoked row.
    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()
    KeyRepo._registry["key-1"] = {
        "id": "key-1",
        "scope": "write",
        "key_hash": "0" * 64,
        "revoked_at": revoked_at,
    }
    KeyRepo._by_key[key_value] = "key-1"

    # Override the autouse stub so verify_key consults KeyRepo and
    # honours the `revoked_at` column.
    from taskq_api.service import auth as _auth

    def _revocation_aware_verify(raw: str, hashed: str) -> bool:
        if not raw or not hashed:
            return False
        key_id = KeyRepo._by_key.get(raw)
        if key_id is None:
            return False
        row = KeyRepo._registry.get(key_id)
        if row is None:
            return False
        if row.get("revoked_at") is not None:
            return False
        return True

    monkeypatch.setattr(_auth, "verify_key", _revocation_aware_verify)

    response = await client.post(
        "/v1/tasks",
        json={"name": "revoked-build", "command": "echo x"},
        headers={"X-API-Key": key_value},
    )

    result_status_code = response.status_code
    assert result_status_code == 401
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )
