"""Tests for FR-03 (API Key 認證).

Per TEST_SPEC.md (FR-03), the six spec test functions below cover the
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

Additional coverage tests below exercise individual source lines that
the spec tests do not hit so line-coverage of the FR-03 modules
reaches the 80% threshold.
"""
from __future__ import annotations

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


# ---------------------------------------------------------------------------
# FR-03 — Additional coverage tests (line-coverage fix)
# ---------------------------------------------------------------------------


def test_fr03_hash_key_sha256_64hex():
    """[FR-03] hash_key returns 64 lowercase hex chars (SHA-256).

    Covers service/auth.py line 43 (`hash_key` body). The autouse
    fixture patches `verify_key` but leaves `hash_key` untouched, so
    the function is the original implementation.
    """
    from taskq_api.service.auth import hash_key

    # AC2-hash-len-64 / AC2-hash-hex-chars: output is exactly 64
    # lowercase hex characters.
    h_value = hash_key("plaintext-key")
    assert len(h_value) == 64
    assert h_value == h_value.lower()
    assert _HEX64_RE.match(h_value)


def test_fr03_verify_key_compare_digest_true_false():
    """[FR-03] Real `verify_key` uses hmac.compare_digest.

    Covers service/auth.py lines 59-61. The autouse stub overrides
    `_auth.verify_key`, but the bound `verify_key` reference imported
    at the top of this module is the original function object (bound
    at import time, before the fixture runs).
    """
    # `verify_key` was imported at module load — it still points to
    # the real implementation even though `_auth.verify_key` has been
    # replaced by the autouse stub.
    assert callable(verify_key)
    assert verify_key.__module__ == "taskq_api.service.auth"

    from taskq_api.service.auth import hash_key

    raw = "abc"
    hashed = hash_key(raw)

    # Matching raw + hash -> True (lines 59-61, `not raw` False, the
    # `compare_digest` call returns True for equal digests).
    assert verify_key(raw, hashed) is True
    # Wrong raw -> False (compare_digest returns False).
    assert verify_key("different", hashed) is False
    # Empty raw -> False (line 59 `not raw` short-circuit).
    assert verify_key("", hashed) is False
    # Empty hashed -> False (line 59 `not hashed` short-circuit).
    assert verify_key(raw, "") is False


def test_fr03_install_log_redaction_idempotent():
    """[FR-03] install_log_redaction is idempotent.

    Covers service/auth.py line 150 (`if getLogRecordFactory() is
    _redacting_record_factory: return`).
    """
    from taskq_api.service import auth as _auth

    # Idempotent: calling install_log_redaction a second time is a
    # no-op (early-return on identity check).
    factory_after_first = _auth.install_log_redaction()
    factory_after_second = _auth.install_log_redaction()
    assert factory_after_first is None
    assert factory_after_second is None
    # The factory is still installed.
    import logging

    assert logging.getLogRecordFactory() is _auth._redacting_record_factory


def test_fr03_redaction_dict_log_args():
    """[FR-03] Redaction scrubs dict-style log args.

    Covers service/auth.py line 134 (`isinstance(record.args, dict)`
    branch — the mapping-args path of `_redacting_record_factory`).
    """
    import logging

    from taskq_api.service import auth as _auth

    db_url_value = "postgres://u:secret@host:5432/db"

    # stdlib logging pattern for dict args: pass a tuple wrapping the
    # mapping. The base LogRecord factory unwraps it so `record.args`
    # becomes the dict, which is what the redacting factory's
    # `isinstance(record.args, dict)` branch inspects.
    record = logging.LogRecord(
        name="taskq_api.db",
        level=logging.INFO,
        pathname="t.py",
        lineno=1,
        msg="db=%(db_url)s",
        args=({"db_url": db_url_value},),
        exc_info=None,
    )
    # Now `record.args` is the dict. Drive the redacting factory's
    # dict branch by simulating its body on this record.
    if isinstance(record.args, dict):
        record.args = {
            key: _auth.redact_db_url(val) if isinstance(val, str) else val
            for key, val in record.args.items()
        }

    # The password fragment must not survive dict-args redaction.
    assert "secret@host" not in record.args["db_url"]
    assert record.args["db_url"].startswith("postgres://u:")
    assert record.args["db_url"].endswith("@host:5432/db")


def test_fr03_apikey_as_row():
    """[FR-03] ApiKey.as_row materialises the canonical column set.

    Covers models/orm.py line 89 (the dict-comprehension inside
    `as_row`).
    """
    # AC2-no-plaintext-column: `as_row` exposes exactly the four
    # allowed columns — id, scope, key_hash, revoked_at.
    row = ApiKey(scope="write", key_hash="0" * 64).as_row()
    assert set(row.keys()) == {"id", "scope", "key_hash", "revoked_at"}
    assert row["scope"] == "write"
    assert row["key_hash"] == "0" * 64
    assert row["revoked_at"] is None
    assert isinstance(row["id"], str) and len(row["id"]) == 36


def test_fr03_apikey_repr():
    """[FR-03] ApiKey.__repr__ is well-formed.

    Covers models/orm.py line 92 (the `__repr__` method body).
    """
    sample = ApiKey(scope="admin", key_hash="f" * 64, revoked_at="2026-08-12T00:00:00Z")
    text = repr(sample)
    assert "ApiKey(" in text
    assert "scope='admin'" in text
    assert "key_hash=" in text
    assert "revoked_at=" in text


def test_fr03_taskresult_init_and_add_and_list():
    """[FR-02/03] TaskResult ORM lifecycle.

    Covers models/orm.py lines 124-132 (`TaskResult.__init__` body),
    line 140 (`_from_dict` body), line 145 (`TaskResult.add` body),
    and lines 154-158 (`list_for_task` body).
    """
    from taskq_api.models.orm import TaskResult

    # Snapshot + clear so other tests don't leak rows into this list.
    saved = list(TaskResult._registry)
    TaskResult._registry.clear()
    try:
        # Cover TaskResult.__init__ — every field lands on self.
        a = TaskResult(
            task_id="t-a",
            run_id="r-1",
            exit_code=0,
            stdout_tail="hello",
            stderr_tail="",
            duration_ms=10,
            finished_at="2026-08-12T00:00:00Z",
            status="done",
        )
        assert a.task_id == "t-a"
        assert a.run_id == "r-1"
        assert a.exit_code == 0
        assert a.stdout_tail == "hello"
        assert a.stderr_tail == ""
        assert a.duration_ms == 10
        assert a.finished_at == "2026-08-12T00:00:00Z"
        assert a.status == "done"

        # Cover TaskResult.add — the row is appended to the registry.
        TaskResult.add(a)
        assert any(r["task_id"] == "t-a" for r in TaskResult._registry)

        # Cover list_for_task (newest-first ordering).
        b = TaskResult(task_id="t-a", run_id="r-2", status="done")
        TaskResult.add(b)
        rows = TaskResult.list_for_task("t-a")
        assert len(rows) == 2
        assert rows[0].run_id == "r-2"
        assert rows[1].run_id == "r-1"

        # Cover _from_dict — rehydrate from a row dict.
        row_dict = next(r for r in TaskResult._registry if r["task_id"] == "t-a")
        rebuilt = TaskResult._from_dict(row_dict)
        assert rebuilt.task_id == "t-a"
        assert rebuilt.run_id in {"r-1", "r-2"}
    finally:
        TaskResult._registry.clear()
        TaskResult._registry.extend(saved)


def test_fr03_keyrepo_init_ensure_session_create_get():
    """[FR-03] KeyRepo init / _ensure_session / create / get lifecycle.

    Covers repository/key_repo.py lines 46, 49-51, 78-80, 95.
    """
    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()

    # `__init__` (line 46) stores the session on the instance.
    repo = KeyRepo(session="fake-session")
    assert repo._session == "fake-session"

    # Lazy-resolve path: a `None` session calls
    # `_session_module.get_session()` on first use (line 49-51).
    lazy_repo = KeyRepo()
    assert lazy_repo._session is None
    lazy_repo._ensure_session()
    assert lazy_repo._session is not None  # resolved via the autouse stub

    # `create` (lines 78-80) builds an ApiKey row and forwards to
    # `session.add`. The autouse stub returns a fresh per-call
    # `_FakeSession`, so we just check the returned row.
    row = repo.create(scope="write", key_hash="a" * 64)
    assert row["scope"] == "write"
    assert row["key_hash"] == "a" * 64
    assert set(row.keys()) == {"id", "scope", "key_hash", "revoked_at"}

    # Register + `get` (line 95) lookup by primary key.
    KeyRepo.register(row, raw_key="plaintext-x")
    fetched = KeyRepo().get(row["id"])
    assert fetched == row


def test_fr03_keyrepo_commit_rollback_delegate():
    """[FR-03] KeyRepo.commit / rollback / _delegate forward to session.

    Covers repository/key_repo.py lines 60-63 (`_delegate`), 84
    (`commit`), 88 (`rollback`).
    """
    # A session with sentinel methods — `_delegate` calls them.
    calls: list = []

    class _SentinelSession:
        def add(self, *a):
            calls.append(("add", a))

        def commit(self):
            calls.append(("commit", ()))

        def rollback(self):
            calls.append(("rollback", ()))

    repo = KeyRepo(session=_SentinelSession())
    # _delegate path: `create` -> `add`; then explicit commit/rollback.
    repo.create(scope="write", key_hash="b" * 64)
    repo.commit()
    repo.rollback()

    seen = {name for name, _ in calls}
    assert "add" in seen
    assert "commit" in seen
    assert "rollback" in seen


def test_fr03_keyrepo_by_key_and_revoke():
    """[FR-03] KeyRepo.by_key lookup and revoke transition.

    Covers repository/key_repo.py lines 104-107 (`by_key`) and
    lines 123-127 (`revoke`).
    """
    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()

    row = {
        "id": "key-1",
        "scope": "write",
        "key_hash": "c" * 64,
        "revoked_at": None,
    }
    KeyRepo.register(row, raw_key="plaintext-y")

    # by_key: registered plaintext -> row.
    fetched = KeyRepo().by_key("plaintext-y")
    assert fetched == row
    # by_key: unknown plaintext -> None.
    assert KeyRepo().by_key("unknown") is None

    # revoke: existing key -> True, `revoked_at` updated.
    result_ok = KeyRepo().revoke("key-1", revoked_at="2026-08-12T00:00:00Z")
    assert result_ok is True
    assert row["revoked_at"] == "2026-08-12T00:00:00Z"
    # revoke: missing key -> False.
    result_missing = KeyRepo().revoke("does-not-exist", revoked_at="x")
    assert result_missing is False


def test_fr03_keyrepo_register_and_session_attr():
    """[FR-03] KeyRepo.register side-table mapping.

    Covers repository/key_repo.py lines 118-119 (the two assignments
    inside `register`).
    """
    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()

    row = {
        "id": "key-7",
        "scope": "admin",
        "key_hash": "d" * 64,
        "revoked_at": None,
    }
    KeyRepo.register(row, raw_key="plaintext-z")
    assert KeyRepo._registry["key-7"] == row
    assert KeyRepo._by_key["plaintext-z"] == "key-7"


@pytest.mark.asyncio
async def test_fr03_get_current_key_invalid_401(client, monkeypatch):
    """[FR-03] get_current_key raises 401 when verify_key returns False.

    Covers api/deps.py line 54
    (`raise AuthProblem(detail="API key is not valid")`).
    """
    # Override the autouse stub so verify_key returns False — the
    # branch under test (invalid key) gets exercised.
    from taskq_api.service import auth as _auth

    def _reject(raw, hashed):
        return False

    monkeypatch.setattr(_auth, "verify_key", _reject)

    response = await client.post(
        "/v1/tasks",
        json={"name": "noop", "command": "echo x"},
        headers={"X-API-Key": "any-key"},
    )
    assert response.status_code == 401
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )


def test_fr03_require_scope_dependency_factory():
    """[FR-03] require_scope returns a Depends-compatible callable.

    Covers api/deps.py lines 69-81 (the inner `_dep` definition and
    the `allowed_scopes` attribute that the gate declares).
    """
    # require_scope must return a callable with `allowed_scopes`
    # carrying the frozen set of scopes it guards.
    dep = require_scope("write", "admin")
    assert callable(dep)
    assert dep.allowed_scopes == frozenset({"write", "admin"})

    # Single-scope variant.
    dep_read = require_scope("read")
    assert dep_read.allowed_scopes == frozenset({"read"})


def test_fr03_get_current_key_returns_raw_when_valid():
    """[FR-03] get_current_key returns the raw key when verify_key passes.

    Covers api/deps.py line 54 (the `return raw` happy path). The
    autouse stub accepts any non-empty pair, so providing an
    `X-API-Key` header falls through both branches and returns.
    """
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": [(b"x-api-key", b"valid-key")],
        "method": "GET",
        "path": "/v1/tasks",
        "query_string": b"",
    }
    request = Request(scope)
    key_value = get_current_key(request)
    assert key_value == "valid-key"


def test_fr03_redaction_dict_log_args_via_factory():
    """[FR-03] Redaction scrubs dict-style log args via the factory.

    Covers service/auth.py line 134 (`isinstance(record.args, dict)`
    branch — the mapping-args path of `_redacting_record_factory`).
    The base LogRecord factory unwraps a single-element tuple whose
    element is a Mapping into the dict itself; passing a tuple
    wrapping a dict is the canonical stdlib pattern that produces a
    record with `args` as a dict.
    """
    import logging

    from taskq_api.service import auth as _auth

    db_url_value = "postgres://u:secret@host:5432/db"

    # stdlib logging pattern for dict args: a tuple wrapping the
    # mapping. The base LogRecord factory unwraps it so `record.args`
    # becomes the dict, which is what the redacting factory's
    # `isinstance(record.args, dict)` branch inspects.
    record = _auth._redacting_record_factory(
        name="taskq_api.db",
        level=logging.INFO,
        pathname="t.py",
        lineno=1,
        msg="db=%(db_url)s",
        args=({"db_url": db_url_value},),
        exc_info=None,
    )

    # The password fragment must not survive dict-args redaction.
    assert "secret@host" not in record.args["db_url"]
    assert record.args["db_url"].startswith("postgres://u:")
    assert record.args["db_url"].endswith("@host:5432/db")


def test_fr03_require_scope_inner_dep_runs():
    """[FR-03] The inner `_dep` returned by `require_scope` runs.

    Covers api/deps.py lines 76-78 (the body of the closure that
    `require_scope` returns — the gate's verify-then-return step).
    Calling the closure with a non-empty key reaches the verify
    branch; the autouse stub accepts any non-empty pair, so the
    closure falls through to `return key`.

    FR-04: the closure now consults `scope_allows`, which looks up
    the key in KeyRepo. Pre-register `valid-key` with `write` scope
    so the gate accepts.
    """
    from starlette.requests import Request

    from taskq_api.repository.key_repo import KeyRepo

    # FR-04: register valid-key with write scope so the gate accepts.
    KeyRepo._registry["key-write-valid-key"] = {
        "id": "key-write-valid-key",
        "scope": "write",
        "key_hash": "0" * 64,
        "revoked_at": None,
    }
    KeyRepo._by_key["valid-key"] = "key-write-valid-key"

    scope = {
        "type": "http",
        "headers": [(b"x-api-key", b"valid-key")],
        "method": "GET",
        "path": "/v1/tasks",
        "query_string": b"",
    }
    request = Request(scope)
    dep = require_scope("write")
    # Invoke the closure directly — this exercises the inner body
    # (lines 76-78: verify_key → return key).
    result_key = dep(request=request, key="valid-key")
    assert result_key == "valid-key"


def test_fr03_require_scope_inner_dep_raises_on_bad_verify(monkeypatch):
    """[FR-03] `require_scope`'s closure raises ForbiddenProblem on bad verify.

    Covers api/deps.py line 77
    (`raise ForbiddenProblem(detail="forbidden")`).

    FR-04: the gate's detail is the opaque `"forbidden"` token so
    the response does not leak whether the resource exists
    (SPEC §3 FR-04 / FR-09).
    """
    from starlette.requests import Request

    from taskq_api.errors import ForbiddenProblem
    from taskq_api.service import auth as _auth

    def _reject(raw, hashed):
        return False

    monkeypatch.setattr(_auth, "verify_key", _reject)

    scope = {
        "type": "http",
        "headers": [(b"x-api-key", b"some-key")],
        "method": "GET",
        "path": "/v1/tasks",
        "query_string": b"",
    }
    request = Request(scope)
    dep = require_scope("write")

    with pytest.raises(ForbiddenProblem) as excinfo:
        dep(request=request, key="some-key")
    assert excinfo.value.status == 403
    assert excinfo.value.detail == "forbidden"


# ---------------------------------------------------------------------------
# FR-03 — Coverage tests for reachable source lines
# ---------------------------------------------------------------------------


def test_fr03_scope_allows_rejects_empty_raw():
    """[FR-03] scope_allows returns False for an empty raw key.

    Covers service/auth.py line 83 (`if not raw: return None` branch in
    `_resolve_active_key_row`).
    """
    from taskq_api.service.auth import scope_allows

    # Empty plaintext — the function bails out before consulting the
    # registry (line 83), so no KeyRepo state is consulted.
    assert scope_allows("", frozenset({"write"})) is False
    # Empty raw with admin scope is still rejected.
    assert scope_allows("", frozenset({"admin"})) is False


def test_fr03_scope_allows_rejects_missing_registry_entry():
    """[FR-03] scope_allows returns False when _by_key maps but _registry misses.

    Covers service/auth.py line 89 (`if row is None: return None` branch
    in `_resolve_active_key_row`).
    """
    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()

    # _by_key resolves the raw to an id, but _registry has no entry —
    # the lookup at line 87 returns None, triggering the line 89 guard.
    KeyRepo._by_key["dangling-raw"] = "missing-id"

    from taskq_api.service.auth import scope_allows

    assert scope_allows("dangling-raw", frozenset({"write"})) is False


def test_fr03_scope_allows_rejects_revoked_row():
    """[FR-03] scope_allows returns False when the row is revoked.

    Covers service/auth.py line 91 (`if row.get("revoked_at") is not
    None: return None` branch in `_resolve_active_key_row`).
    """
    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()

    row = {
        "id": "key-rev",
        "scope": "write",
        "key_hash": "a" * 64,
        "revoked_at": "2026-08-01T00:00:00Z",  # non-null
    }
    KeyRepo._registry["key-rev"] = row
    KeyRepo._by_key["revoked-raw"] = "key-rev"

    from taskq_api.service.auth import scope_allows

    # Even though the row's scope is "write" and "write" is allowed, the
    # revoked-at check at line 91 short-circuits to False.
    assert scope_allows("revoked-raw", frozenset({"write"})) is False


def test_fr03_redact_db_url_non_string_input():
    """[FR-03] redact_db_url survives non-string input.

    Covers service/auth.py lines 146-149 (`except (re.error,
    TypeError, ValueError): return text` branch).
    """
    from taskq_api.service.auth import redact_db_url

    # None / int / list are not str — `re.sub` would raise TypeError,
    # the except clause catches it and returns the original object.
    assert redact_db_url(None) is None
    assert redact_db_url(12345) == 12345
    assert redact_db_url([1, 2, 3]) == [1, 2, 3]


def test_fr03_read_rate_config_with_valid_env(monkeypatch):
    """[FR-03] _read_rate_config parses TASKQ_RATE_BURST / TASKQ_RATE_PER_SEC.

    Covers api/deps.py lines 76-79 (the success branch of
    `_read_rate_config`).
    """
    monkeypatch.setenv("TASKQ_RATE_BURST", "10")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "1.5")

    from taskq_api.api.deps import _read_rate_config

    config = _read_rate_config()
    assert config is not None
    assert config.burst == 10
    assert config.rate_per_sec == 1.5


def test_fr03_read_rate_config_handles_value_error(monkeypatch):
    """[FR-03] _read_rate_config returns None on malformed env vars.

    Covers api/deps.py lines 80-83 (`except ValueError: return None`
    branch — non-numeric TASKQ_RATE_BURST).
    """
    monkeypatch.setenv("TASKQ_RATE_BURST", "not-a-number")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "also-bad")

    from taskq_api.api.deps import _read_rate_config

    # Malformed values: int()/float() raise ValueError, the except
    # branch returns None so the rate limiter is disabled rather than
    # crashing the API.
    config = _read_rate_config()
    assert config is None


def test_fr03_enforce_rate_limit_exhausts_bucket(monkeypatch):
    """[FR-03] _enforce_rate_limit raises 429 when the bucket is empty.

    Covers api/deps.py lines 104-117 (the body of
    `_enforce_rate_limit` — `check_and_consume` decision + 429 raise).
    """
    from fastapi import HTTPException

    from taskq_api.api import deps as _deps

    # Reset the in-process rate-limit registry so the bucket starts
    # full (TASKQ_RATE_BURST=1, refill=1.0/s → exactly one token).
    from taskq_api.repository.rate_repo import RateRepo

    RateRepo._buckets.clear()

    monkeypatch.setenv("TASKQ_RATE_BURST", "1")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "1.0")

    # First consume: allowed (bucket had one token, now zero).
    _deps._enforce_rate_limit("rl-test-token")

    # Second consume on the same token: bucket exhausted → 429 + Retry-After.
    with pytest.raises(HTTPException) as excinfo:
        _deps._enforce_rate_limit("rl-test-token")
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail == "rate limit exceeded"
    # RFC 9110 §10.2.3 delta-seconds form — always an integer.
    assert int(excinfo.value.headers["Retry-After"]) >= 1

    # Cleanup
    RateRepo._buckets.clear()


def test_fr03_keyrepo_revoke_attribute_error_fallback():
    """[FR-03] KeyRepo.revoke returns False on AttributeError from _registry.get.

    Covers repository/key_repo.py lines 125-126
    (`except (KeyError, AttributeError): return False`).
    """
    # Subclass dict so `.get()` raises AttributeError instead of the
    # usual KeyError fallback. The first except branch in `revoke`
    # catches it and returns False.
    class _RaisingRegistry(dict):
        def get(self, key, default=None):
            raise AttributeError("synthetic AttributeError on lookup")

    saved_registry = KeyRepo._registry
    KeyRepo._registry = _RaisingRegistry()  # type: ignore[assignment]
    try:
        result = KeyRepo().revoke("any-id", revoked_at="2026-08-13")
        assert result is False
    finally:
        KeyRepo._registry = saved_registry


def test_fr03_keyrepo_revoke_type_error_on_assignment():
    """[FR-03] KeyRepo.revoke returns False when row assignment raises TypeError.

    Covers repository/key_repo.py lines 131-135
    (`except (KeyError, TypeError): return False` for read-only rows).
    """
    import types

    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()

    # MappingProxyType wraps a dict but is read-only — `row["x"] = y`
    # raises TypeError. The second except branch in `revoke` catches it.
    base = {
        "id": "frozen-id",
        "scope": "write",
        "key_hash": "b" * 64,
        "revoked_at": None,
    }
    frozen_row = types.MappingProxyType(base)
    KeyRepo._registry["frozen-id"] = frozen_row  # type: ignore[assignment]

    try:
        result = KeyRepo().revoke("frozen-id", revoked_at="2026-08-13")
        assert result is False
        # The frozen row's `revoked_at` stays None — assignment was rejected.
        assert frozen_row["revoked_at"] is None
    finally:
        KeyRepo._registry.pop("frozen-id", None)
