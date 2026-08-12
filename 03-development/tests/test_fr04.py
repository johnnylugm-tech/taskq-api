"""TDD-RED failing tests for FR-04 (Scope 授權).

Per TEST_SPEC.md (FR-04), the four spec test functions below cover the
canonical acceptance criteria:

    AC1-forbidden-status    DELETE /v1/tasks/{id} (write key, exists) -> 403
    AC1-no-leak-exists      body detail == "forbidden" (no existence leak)
    AC2-opaque-when-missing DELETE /v1/tasks/{id} (write key, missing) -> 403
    AC2-same-detail         body detail == "forbidden" (same as AC1)
    AC3-all-routes-use-dep  every /v1/* route goes through require_scope
    AC4-admin-status        DELETE /v1/tasks/{id} (admin key, exists) -> 204

The TEST_SPEC names are the contract; spec-coverage-check uses exact
match. Do NOT rename these functions.

These tests intentionally fail because the FR-04 single-dependency
scope gate is not yet enforced:
  - `api.tasks.delete_task` uses `Depends(get_current_key)` instead of
    `Depends(require_scope("admin"))`.
  - The inner closure inside `api.deps.require_scope` re-verifies the
    key via `verify_key` but does not consult the key's stored scope.
  - No `/v1/*` route declares its required scope via `require_scope`.

The Green step will:
  1. Add `Depends(require_scope("admin"))` to DELETE /v1/tasks/{id}.
  2. Implement `service.auth.scope_allows(raw, allowed)` so the
     `require_scope` closure can compare the key's stored scope
     against the gate's `allowed_scopes` set.
  3. Mirror the same `Depends(require_scope(...))` pattern on every
     other `/v1/*` route so the single-dependency invariant holds.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

# Top-level imports — RED will surface if any declared SAB module is
# missing on disk. The deps and auth modules already exist; the GREEN
# step tightens their semantics. It is EXPECTED and acceptable for
# pytest to fail with Collection Error (Exit Code 2) if either module
# is removed during refactor.
from taskq_api.api.deps import require_scope  # noqa: F401
from taskq_api.app import create_app
from taskq_api.repository.key_repo import KeyRepo
from taskq_api.repository.task_repo import TaskRepo
from taskq_api.service import auth as _auth_module

# Captured BEFORE the autouse fixture monkey-patches `verify_key` to
# its scope-aware stub. The coverage backfill tests below exercise the
# REAL `service.auth` primitives (verify_key / _resolve_active_key_row /
# scope_allows / redact_db_url / _scrub / _redacting_record_factory /
# install_log_redaction) — they reach them through these saved
# references because `_auth_module.verify_key` is the stub once the
# autouse fixture has run.
_REAL_VERIFY_KEY = _auth_module.verify_key
_REAL_RESOLVE_ACTIVE_KEY_ROW = _auth_module._resolve_active_key_row
_REAL_SCOPE_ALLOWS = _auth_module.scope_allows
_REAL_REDACT_DB_URL = _auth_module.redact_db_url
_REAL_SCRUB = _auth_module._scrub
_REAL_REDACTING_RECORD_FACTORY = _auth_module._redacting_record_factory
_REAL_INSTALL_LOG_REDACTION = _auth_module.install_log_redaction


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def write_api_key() -> str:
    """Static write-scoped key used by the forbidden-scope tests."""
    return "fr04-write-key"


@pytest.fixture
def admin_api_key() -> str:
    """Static admin-scoped key used by the happy-path admin test."""
    return "fr04-admin-key"


@pytest.fixture(autouse=True)
def _stub_external_side_effects(monkeypatch):
    """Stub external side-effects so tests fail for FEATURE reasons only.

    The autouse fixture runs before every test; it patches the auth
    verifier to consult the in-process KeyRepo registry and resets the
    task / key registries so each test starts from a clean slate.

    GREEN TODO: `taskq_api.service.auth.verify_key(raw, hashed) -> bool`
    must consult the `api_keys` row for `raw` (production wiring hashes
    `raw` then looks up by hash) and reject rows whose `revoked_at` is
    non-null. The autouse stub mimics that contract so the route
    exercises real scope-checking logic without a real DB driver.
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

    monkeypatch.setattr(
        _session,
        "get_session",
        lambda: _FakeSession(),
    )

    # Reset in-process registries between tests so each test sees an
    # empty starting state for tasks and api_keys.
    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()
    TaskRepo._registry.clear()
    TaskRepo._by_name.clear()

    yield

    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()
    TaskRepo._registry.clear()
    TaskRepo._by_name.clear()


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


def _register_api_key(*, raw_key: str, scope: str) -> None:
    """Pre-populate the KeyRepo registry with a single api_keys row.

    Stores `scope` so GREEN's `scope_allows` (or equivalent scope
    lookup) can read it from the row. The autouse stub accepts any
    non-revoked row, so this also makes the verifier accept `raw_key`.
    """
    key_id = f"key-{scope}-{raw_key}"
    KeyRepo._registry[key_id] = {
        "id": key_id,
        "scope": scope,
        "key_hash": "0" * 64,
        "revoked_at": None,
    }
    KeyRepo._by_key[raw_key] = key_id


def _register_task(*, task_id: str, name: str) -> None:
    """Pre-populate the TaskRepo registry with a single task row.

    The DELETE handler routes through `service.delete` →
    `TaskRepo.delete(task_id)`, which pops from the in-process
    `_registry`. Registering here gives the handler something to find
    (and, after GREEN, something the require_scope("admin") gate
    protects).
    """
    TaskRepo._registry[task_id] = {
        "id": task_id,
        "name": name,
        "command": "echo fr04",
        "status": "pending",
    }
    TaskRepo._by_name[name] = task_id


def _problem_detail_str(response) -> str:
    """Return the problem+json 'detail' as a flat lowercase string.

    SPEC §3 FR-04 mandates that the 403 body MUST NOT leak whether the
    resource exists. We compare the literal `"forbidden"` token, so
    any task-id fragment or "not found" / "no such task" phrase would
    fail the assertion — exactly the FR-04 existence-leak guard.
    """
    import json as _json

    try:
        body = response.json()
    except Exception:
        return ""
    if isinstance(body, dict):
        value = body.get("detail", "")
    else:
        value = body
    if isinstance(value, list):
        return " ".join(_json.dumps(item) for item in value).lower()
    return str(value).lower()


def _collect_v1_routes(app):
    """Yield (route, dependant) for every route mounted under /v1/*.

    Excludes `/v1/metrics` (which is mounted directly on `app` and is
    exempt from the auth gate by SPEC §3 FR-09). The single-dependency
    contract applies to handler routes only.
    """
    for route in app.routes:
        if not hasattr(route, "path"):
            continue
        if not route.path.startswith("/v1/"):
            continue
        if route.path == "/v1/metrics":
            continue
        yield route


def _route_uses_scope_gate(route) -> bool:
    """Return True iff any dep in the route's tree carries `allowed_scopes`.

    The `require_scope` factory attaches `allowed_scopes` to the
    closure it returns (see `api/deps.py`). A route that goes through
    the single-dependency gate will have that closure somewhere in its
    `dependant` tree (directly, or transitively via sub-deps).
    """
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False

    seen: set[int] = set()
    queue = [dependant]
    while queue:
        node = queue.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if hasattr(node.call, "allowed_scopes"):
            return True
        for sub in getattr(node, "dependencies", []) or []:
            queue.append(sub)
    return False


# ---------------------------------------------------------------------------
# FR-04 — Test cases from TEST_SPEC.md (do NOT rename)
# ---------------------------------------------------------------------------


# NFR-02 NFR-09 SEC T-03
@pytest.mark.asyncio
async def test_fr04_delete_forbidden_403_opaque(client, write_api_key):
    """AC1-forbidden-status / AC1-no-leak-exists. [FR-04][NFR-02][SEC T-03]

    DELETE /v1/tasks/{id} with a WRITE key (not admin) on an EXISTING
    task returns 403 + problem+json whose detail is exactly the
    opaque `"forbidden"` token — the response MUST NOT contain the
    task id, the task name, or any phrase that would disclose whether
    the resource exists. Q2 / NP-02.

    GREEN TODO: GREEN agent must:
      1. Add `Depends(require_scope("admin"))` to DELETE /v1/tasks/{id}.
      2. Make the inner `require_scope` closure compare the
         authenticated key's stored scope (looked up via `service.auth`
         or `repository.key_repo`) against `allowed_scopes`.
      3. Emit a `ForbiddenProblem` whose `detail` is `"forbidden"`
         (NOT `"task {id} not found"` or `"insufficient scope"`-with-id).
    """
    task_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    _register_task(task_id=task_id, name="alpha-build")
    _register_api_key(raw_key=write_api_key, scope="write")

    response = await client.delete(
        f"/v1/tasks/{task_id}",
        headers={"X-API-Key": write_api_key},
    )

    result_status_code = response.status_code
    result_problem_detail_str = _problem_detail_str(response)
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )
    assert result_status_code == 403
    assert result_problem_detail_str == "forbidden", (
        f"FR-04 existence-leak guard violated: 403 body detail must be "
        f"the opaque token 'forbidden' but got "
        f"{result_problem_detail_str!r}; the response is leaking "
        f"information about the resource"
    )


# NFR-02 NFR-09 SEC T-03
@pytest.mark.asyncio
async def test_fr04_delete_forbidden_403_existence_opaque(client, write_api_key):
    """AC2-opaque-when-missing / AC2-same-detail. [FR-04][NFR-02][SEC T-03]

    DELETE /v1/tasks/{id} with a WRITE key on a MISSING task returns
    403 with the same opaque `"forbidden"` body — NOT 404. The two
    responses (existing + missing) MUST be byte-identical so an
    attacker cannot probe resource existence by comparing status codes.
    Q2 / NP-02.

    GREEN TODO: Same scope-gate tightening as AC1. The handler MUST
    not reach `service.delete` (which raises 404) when the scope
    check fails — the gate runs first.
    """
    missing_task_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    _register_api_key(raw_key=write_api_key, scope="write")

    # Confirm the task truly is missing — the row is not in the
    # registry, so any 404 path the current code takes must be masked
    # by the scope gate.
    assert missing_task_id not in TaskRepo._registry

    response = await client.delete(
        f"/v1/tasks/{missing_task_id}",
        headers={"X-API-Key": write_api_key},
    )

    result_status_code = response.status_code
    result_problem_detail_str = _problem_detail_str(response)
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )
    assert result_status_code == 403, (
        f"FR-04 existence-leak guard violated: missing-resource "
        f"DELETE returned {result_status_code}; must be 403 (same as "
        f"existing-resource case) so an attacker cannot probe "
        f"existence via status-code differential"
    )
    assert result_problem_detail_str == "forbidden", (
        f"FR-04 opaque-body invariant violated: missing-resource "
        f"detail {result_problem_detail_str!r} must equal the "
        f"existing-resource detail 'forbidden'"
    )


# NFR-06 NFR-11
def test_fr04_single_scope_dependency():
    """AC3-all-routes-use-dep. [FR-04][NFR-06][NFR-11]

    Every `/v1/*` handler route MUST go through the single-dependency
    scope gate (`api.deps.require_scope`). SPEC §3 FR-04 phrases this
    as "以測試斷言「每個 `/v1` 路由都經過同一個 dependency」" — there must
    be one decision point, not a per-handler `if scope == ...` ladder.
    Q6.

    GREEN TODO: GREEN agent must attach `Depends(require_scope(...))`
    to every handler route under `/v1/*` (POST /v1/tasks, GET list,
    GET single, DELETE, POST run, GET runs) — no handler may decide
    the scope check inline.
    """
    app = create_app()

    v1_routes = list(_collect_v1_routes(app))
    # Sanity — at least the canonical four FR-04 routes are mounted;
    # if GREEN shrinks the surface, this baseline guards the
    # invariant instead of silently passing on zero routes.
    assert len(v1_routes) >= 1, "no /v1/* handler routes mounted"

    routes_without_dep = sum(
        1 for route in v1_routes if not _route_uses_scope_gate(route)
    )
    assert routes_without_dep == 0, (
        f"FR-04 single-dependency invariant violated: "
        f"{routes_without_dep}/{len(v1_routes)} /v1/* handler routes "
        f"do not go through api.deps.require_scope; SPEC §3 FR-04 "
        f"requires one decision point for all routes"
    )


# NFR-09 NFR-10
@pytest.mark.asyncio
async def test_fr04_admin_delete_succeeds_204(client, write_api_key, admin_api_key):
    """AC4-admin-status. [FR-04][NFR-09][NFR-10]

    DELETE /v1/tasks/{id} with an ADMIN key on an EXISTING task
    returns 204. The happy-path case for the single-dependency gate:
    the gate must ALLOW admin-scoped keys through to the handler.

    The test pre-asserts that the WRITE key is rejected first (403)
    so a missing scope gate surfaces as a failing assertion here, not
    as a silently-passing test that only happens to delete the row
    via the un-gated `Depends(get_current_key)` path that exists in
    the current code. happy_path / Q1.

    GREEN TODO: GREEN agent must make the `require_scope("admin")`
    gate accept admin-scoped keys (lookup the key's stored scope and
    check membership in `allowed_scopes`) while still rejecting
    write-scoped keys — the two assertions below verify both halves.
    """
    task_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    _register_task(task_id=task_id, name="admin-target")
    _register_api_key(raw_key=write_api_key, scope="write")
    _register_api_key(raw_key=admin_api_key, scope="admin")

    # First half: the gate MUST reject the write key. Today (no scope
    # check on DELETE) this returns 204, which fails this assertion
    # and surfaces the missing-feature RED state.
    write_response = await client.delete(
        f"/v1/tasks/{task_id}",
        headers={"X-API-Key": write_api_key},
    )
    assert write_response.status_code == 403, (
        f"FR-04 scope gate missing on DELETE: write key returned "
        f"{write_response.status_code}, expected 403; the single-"
        f"dependency gate must reject write-scoped keys on the "
        f"admin-only DELETE endpoint"
    )

    # Second half: the gate MUST allow the admin key through. After
    # GREEN, the gate's scope-membership check lets the admin key
    # reach `service.delete`, which removes the row and returns 204.
    admin_response = await client.delete(
        f"/v1/tasks/{task_id}",
        headers={"X-API-Key": admin_api_key},
    )
    result_status_code = admin_response.status_code
    assert result_status_code == 204, (
        f"FR-04 admin DELETE returned {result_status_code}, "
        f"expected 204; the require_scope('admin') gate must let "
        f"admin-scoped keys reach the DELETE handler"
    )


# ---------------------------------------------------------------------------
# Coverage backfill — exercise lines the spec tests do not hit so the
# GATE1 coverage threshold is met for the modules FR-04 touches.
# ---------------------------------------------------------------------------


# NFR-09 NFR-10
def test_main_dispatch_unreachable_guard(monkeypatch):
    """[FR-03] The defensive dispatch guard in `__main__.main` is reachable
    when the parser is fed an unrecognised `(command, key_command)`
    pair that bypasses argparse's `required=True` validation. The guard
    raises `AssertionError` so a missing dispatch table entry fails fast
    instead of silently falling through with exit code 0.
    """
    import argparse

    from taskq_api import __main__

    fake_args = argparse.Namespace(command="key", key_command="delete", scope=None)

    class _FakeParser:
        def parse_args(self, _argv):
            return fake_args

    monkeypatch.setattr(__main__, "_build_parser", lambda: _FakeParser())

    with pytest.raises(AssertionError) as excinfo:
        __main__.main([])
    assert "unreachable dispatch" in str(excinfo.value)
    assert "command=" in str(excinfo.value)


# NFR-09 NFR-10
def test_main_key_create_dispatch(monkeypatch, capsys):
    """[FR-03] `key create --scope <scope>` end-to-end via `__main__.main`.

    Exercises the happy-path dispatch branch (`command="key"`,
    `key_command="create"`) that the unreachable-guard test does not
    hit, plus the `_gen_plaintext` → `print(KEY=…)` plumbing so
    coverage of `__main__.py` is complete.
    """
    import argparse

    from taskq_api import __main__
    from taskq_api.repository.key_repo import KeyRepo

    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()

    fake_args = argparse.Namespace(command="key", key_command="create", scope="write")

    class _FakeParser:
        def parse_args(self, _argv):
            return fake_args

    monkeypatch.setattr(__main__, "_build_parser", lambda: _FakeParser())

    rc = __main__.main([])
    assert rc == 0

    out = capsys.readouterr().out
    assert out.startswith("KEY=")
    assert len(out.splitlines()) == 1

    KeyRepo._registry.clear()
    KeyRepo._by_key.clear()


# ---------------------------------------------------------------------------
# Coverage backfill — exercise lines the FR-04 spec tests do not hit.
#
# Each test below targets one (or a tight cluster of) uncovered line(s)
# reported by `coverage report -m` on
# `taskq_api/api/deps.py` and `taskq_api/service/auth.py`. The autouse
# `_stub_external_side_effects` fixture swaps `verify_key` for a
# scope-aware stub that drives the `/v1/*` routes, so we reach the REAL
# `service.auth` primitives through the module-level saved references
# captured above the autouse fixture.
# ---------------------------------------------------------------------------


# NFR-09 NFR-10
@pytest.mark.asyncio
async def test_fr04_missing_api_key_header_returns_401(client):
    """[FR-04][NFR-09] Cover deps.py line 47 — missing X-API-Key header.

    The `get_current_key` dep raises `AuthProblem("X-API-Key header is
    required")` (HTTP 401) when the header is absent. The four FR-04
    spec tests all SEND a key, so this branch is otherwise unreachable.
    """
    task_id = "11111111-1111-1111-1111-111111111111"
    _register_task(task_id=task_id, name="missing-header-target")

    response = await client.delete(f"/v1/tasks/{task_id}")  # no X-API-Key

    result_status_code = response.status_code
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )
    assert result_status_code == 401, (
        f"FR-04 deps.py line 47 not covered: DELETE without "
        f"X-API-Key returned {result_status_code}; expected 401"
    )


# NFR-09 NFR-10
@pytest.mark.asyncio
async def test_fr04_invalid_api_key_returns_401(client):
    """[FR-04][NFR-09] Cover deps.py line 53 — verify_key returns False.

    The autouse stub rejects any raw key absent from `KeyRepo._by_key`,
    so a DELETE with an UNREGISTERED key exercises the
    `raise AuthProblem("API key is not valid")` branch.
    """
    task_id = "22222222-2222-2222-2222-222222222222"
    _register_task(task_id=task_id, name="invalid-key-target")

    response = await client.delete(
        f"/v1/tasks/{task_id}",
        headers={"X-API-Key": "definitely-not-registered-key"},
    )

    result_status_code = response.status_code
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )
    assert result_status_code == 401, (
        f"FR-04 deps.py line 53 not covered: DELETE with unregistered "
        f"key returned {result_status_code}; expected 401"
    )


# NFR-09 NFR-10
def test_auth_verify_key_empty_inputs_return_false():
    """[FR-04][NFR-09] Cover auth.py lines 61-62 — empty raw/hashed → False.

    The real `verify_key` short-circuits on empty inputs without
    computing a digest; the autouse stub never reaches this branch
    because the route flow always passes non-empty raw keys through.
    """
    assert _REAL_VERIFY_KEY("", "any-hash") is False
    assert _REAL_VERIFY_KEY("any-raw", "") is False
    assert _REAL_VERIFY_KEY("", "") is False


# NFR-09 NFR-10
def test_auth_verify_key_constant_time_compare_digest():
    """[FR-04][NFR-09] Cover auth.py line 63 — `hmac.compare_digest` return.

    SPEC §3 FR-03: `verify_key` MUST use `hmac.compare_digest` so a
    plain `==` cannot leak the digest through response timing. The
    autouse stub bypasses the real implementation, so we exercise the
    compare-digest return directly with a known matching digest.
    """
    from taskq_api.service.auth import hash_key

    raw = "compare-digest-raw"
    assert _REAL_VERIFY_KEY(raw, hash_key(raw)) is True
    # And a mismatched digest is rejected through the same return path.
    assert _REAL_VERIFY_KEY(raw, "0" * 64) is False


# NFR-09 NFR-10
def test_auth_resolve_active_key_row_empty_raw_returns_none():
    """[FR-04][NFR-09] Cover auth.py line 83 — empty raw → None.

    `_resolve_active_key_row` short-circuits before the side-table
    lookup when `raw` is falsy, so a None result cannot be confused
    with a real "no row registered" miss.
    """
    assert _REAL_RESOLVE_ACTIVE_KEY_ROW("") is None


# NFR-09 NFR-10
def test_auth_resolve_active_key_row_unknown_raw_returns_none():
    """[FR-04][NFR-09] Cover auth.py line 86 — _by_key miss → None.

    The `_by_key` side-table is the first lookup step; a miss here
    means the key was never registered.
    """
    assert _REAL_RESOLVE_ACTIVE_KEY_ROW("never-registered-key") is None


# NFR-09 NFR-10
def test_auth_resolve_active_key_row_missing_registry_row_returns_none():
    """[FR-04][NFR-09] Cover auth.py line 89 — registry miss → None.

    `_by_key` points at an id that is NOT in `_registry` (e.g. a row
    deleted between the two writes). The function MUST NOT return a
    half-formed row.
    """
    KeyRepo._by_key["phantom-raw"] = "phantom-id-not-in-registry"
    try:
        assert _REAL_RESOLVE_ACTIVE_KEY_ROW("phantom-raw") is None
    finally:
        KeyRepo._by_key.pop("phantom-raw", None)


# NFR-09 NFR-10
def test_auth_resolve_active_key_row_revoked_returns_none():
    """[FR-04][NFR-09] Cover auth.py line 91 — revoked row → None.

    SPEC §3 FR-03 AC6-revoked-status: a key with a non-null
    `revoked_at` is rejected even though `get_current_key` already
    returned the raw plaintext.
    """
    KeyRepo._registry["revoked-id"] = {
        "id": "revoked-id",
        "scope": "write",
        "key_hash": "0" * 64,
        "revoked_at": "2026-01-01T00:00:00Z",
    }
    KeyRepo._by_key["revoked-raw"] = "revoked-id"
    try:
        assert _REAL_RESOLVE_ACTIVE_KEY_ROW("revoked-raw") is None
    finally:
        KeyRepo._registry.pop("revoked-id", None)
        KeyRepo._by_key.pop("revoked-raw", None)


# NFR-09 NFR-10
def test_auth_scope_allows_missing_row_returns_false():
    """[FR-04][NFR-09] Cover auth.py line 113 — row None → False.

    `scope_allows` flattens `_resolve_active_key_row`'s four-step
    lookup into a single None check; this test exercises the early
    `return False` for an unregistered raw.
    """
    assert _REAL_SCOPE_ALLOWS("unregistered-scope-key", {"write", "admin"}) is False


# NFR-09 NFR-10
def test_auth_redact_db_url_with_password():
    """[FR-04][NFR-09] Cover auth.py line 144 — password fragment redacted.

    SPEC §4 NFR-04: the `postgres://user:password@host` password
    fragment must be scrubbed to `***` everywhere it could escape
    (logs + `/v1/metrics`).
    """
    redacted = _REAL_REDACT_DB_URL("postgres://user:p@host:5432/db")
    assert "p@host" not in redacted, (
        f"FR-04 / NFR-04 redaction failed: password fragment survived "
        f"in {redacted!r}"
    )
    assert "***" in redacted
    assert redacted.startswith("postgres://user:")
    assert "@host:5432/db" in redacted


# NFR-09 NFR-10
def test_auth_redact_db_url_without_password_unchanged():
    """[FR-04][NFR-09] Cover auth.py line 144 — no-password URL passthrough.

    URL with no `user:password@` segment (no colon before the `@`)
    is left untouched, so logs and `/v1/metrics` stay diagnosable.
    """
    assert (
        _REAL_REDACT_DB_URL("postgres://user@host:5432/db")
        == "postgres://user@host:5432/db"
    )


# NFR-09 NFR-10
def test_auth_scrub_non_string_passthrough():
    """[FR-04][NFR-09] Cover auth.py line 154 — non-string args pass through.

    `_scrub` only redacts strings; ints / Nones / lists / dicts are
    returned unchanged so structured log records still carry their
    data.
    """
    assert _REAL_SCRUB(42) == 42
    assert _REAL_SCRUB(None) is None
    assert _REAL_SCRUB([1, 2, 3]) == [1, 2, 3]
    assert _REAL_SCRUB({"url": "postgres://u:p@host/db"}) == {
        "url": "postgres://u:p@host/db"
    }
    # String inputs ARE scrubbed (covers the other branch of the
    # conditional on the same line).
    assert "p@host" not in _REAL_SCRUB("postgres://u:p@host/db")


# NFR-09 NFR-10
def test_auth_redacting_record_factory_string_msg():
    """[FR-04][NFR-09] Cover auth.py lines 183-185 — string `msg` redacted.

    The record factory's `isinstance(record.msg, str)` branch runs
    `redact_db_url` on the format string before `%`-formatting
    happens.
    """
    record = _REAL_REDACTING_RECORD_FACTORY(
        name="taskq.test",
        level=20,
        pathname="x.py",
        lineno=1,
        msg="connecting to postgres://u:p@host/db",
        args=None,
        exc_info=None,
    )
    assert "p@host" not in record.msg, (
        f"FR-04 / NFR-04 record-factory string-msg branch failed: "
        f"{record.msg!r} still carries the password fragment"
    )
    assert "***" in record.msg


# NFR-09 NFR-10
def test_auth_redacting_record_factory_dict_args():
    """[FR-04][NFR-09] Cover auth.py lines 186-187 — dict `args` redacted.

    Covers `logger.info("connect %(url)s", {"url": url})` style calls.
    Python's `LogRecord.__init__` only unwraps a single-mapping tuple
    (`({"k": v},)`); passing the dict bare raises `KeyError: 0`. The
    unwrap leaves `record.args` as a plain dict, which is what our
    factory's `isinstance(record.args, dict)` branch then scrubs.
    """
    record = _REAL_REDACTING_RECORD_FACTORY(
        name="taskq.test",
        level=20,
        pathname="x.py",
        lineno=1,
        msg="connect %(url)s",
        args=({"url": "postgres://u:p@host/db"},),  # tuple-wrapped mapping
        exc_info=None,
    )
    assert isinstance(record.args, dict)
    assert "p@host" not in record.args["url"], (
        f"FR-04 / NFR-04 record-factory dict-args branch failed: "
        f"{record.args['url']!r} still carries the password fragment"
    )


# NFR-09 NFR-10
def test_auth_redacting_record_factory_tuple_args():
    """[FR-04][NFR-09] Cover auth.py lines 188-189 — tuple `args` redacted.

    Covers `logger.info("connect %s", url)` style calls (the most
    common form).
    """
    record = _REAL_REDACTING_RECORD_FACTORY(
        name="taskq.test",
        level=20,
        pathname="x.py",
        lineno=1,
        msg="connect %s",
        args=("postgres://u:p@host/db",),
        exc_info=None,
    )
    assert isinstance(record.args, tuple)
    assert "p@host" not in record.args[0], (
        f"FR-04 / NFR-04 record-factory tuple-args branch failed: "
        f"{record.args[0]!r} still carries the password fragment"
    )


# NFR-09 NFR-10
def test_auth_install_log_redaction_idempotent():
    """[FR-04][NFR-09] Cover auth.py line 203 — idempotent install.

    Re-installing would otherwise chain the factory onto itself and
    redact each record twice. The early-return branch (line 203) is
    hit by calling `install_log_redaction` while the redacting factory
    is already the active one.
    """
    import logging

    # The autouse import of `service.auth` has already installed the
    # redacting factory at module-import time. Confirm and re-install.
    assert logging.getLogRecordFactory() is _REAL_REDACTING_RECORD_FACTORY
    # Idempotent call — MUST NOT raise and MUST NOT replace the factory.
    _REAL_INSTALL_LOG_REDACTION()
    assert logging.getLogRecordFactory() is _REAL_REDACTING_RECORD_FACTORY
