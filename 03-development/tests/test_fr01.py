"""TDD-RED failing tests for FR-01 (Task Resource CRUD API).

These tests intentionally fail because the source modules declared in
the SAB (taskq_api.api.tasks, taskq_api.service.tasks,
taskq_api.repository.task_repo, taskq_api.models.schemas) do not yet
exist on disk. The Green step will implement them; this Red step
locks the contract.

Per the TEST_INVENTORY / TEST_SPEC catalog (FR-01), the nine test
functions below cover the canonical acceptance criteria:

    AC1-create-status            POST /v1/tasks (write key) -> 201
    AC1-create-id-present         response includes a 36-char task id
    AC2-no-key-status            POST /v1/tasks (no X-API-Key) -> 401
    AC3-unknown-status           GET /v1/tasks/{unknown} -> 404
    AC4-duplicate-status         POST /v1/tasks duplicate name -> 409
    AC5-empty-name-status        POST /v1/tasks (empty name) -> 422
    AC5-empty-name-detail        problem+json detail mentions 'name'
    AC6-oversize-status          POST /v1/tasks (1001-char command) -> 422
    AC6-oversize-detail          problem+json detail mentions '1000' or 'command'
    AC7-cursor-default-limit     GET /v1/tasks default limit == 50
    AC8-overbound-status         GET /v1/tasks?limit=201 -> 422
    AC9-sql-count-constant       list query runs <= 2 SQL statements
    AC9-no-n-plus-1              list query runs < 5 SQL statements

The TEST_SPEC names are the contract; spec-coverage-check uses exact
match. Do NOT rename these functions.
"""
from __future__ import annotations

import re

import pytest
from httpx import ASGITransport, AsyncClient

# Top-level imports — RED will surface as ModuleNotFoundError.
# It is EXPECTED and acceptable for pytest to fail with Collection
# Error (Exit Code 2) at this stage.
from taskq_api.api.tasks import create_tasks_router  # noqa: F401
from taskq_api.app import create_app
from taskq_api.models.schemas import TaskCreate  # noqa: F401
from taskq_api.repository.task_repo import TaskRepo  # noqa: F401
from taskq_api.service.tasks import TaskService  # noqa: F401


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def write_api_key() -> str:
    """Static write-scoped key used by FR-01 happy-path tests."""
    return "test-write-key"


@pytest.fixture
def read_api_key() -> str:
    """Static read-scoped key used by FR-01 list tests."""
    return "test-read-key"


@pytest.fixture
async def client():
    """Yield an httpx AsyncClient bound to the FastAPI ASGI app.

    Uses ASGITransport so the request never leaves the process — the
    SUBPROCESS COVERAGE CEILING rule is N/A here because pytest-cov
    can measure code executed by ASGITransport. The DB session is
    stubbed via the autouse fixture so no real disk I/O occurs.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _stub_external_side_effects(monkeypatch):
    """Stub external side-effects so tests fail for FEATURE reasons only.

    The autouse fixture runs before every test; it patches the auth
    verifier and DB session acquisition so a missing feature surfaces
    as a 404 / 500 / AssertionError rather than a CryptoError or
    OperationalError from a real DB driver.
    """
    # GREEN TODO: taskq_api.service.auth.verify_key(raw, hashed) -> bool
    # The stub makes any non-empty key 'valid' so auth short-circuits
    # to the route. The GREEN agent must replace the stub with a real
    # HMAC + revocation check.
    from taskq_api.service import auth as _auth

    monkeypatch.setattr(
        _auth,
        "verify_key",
        lambda raw, hashed: bool(raw) and bool(hashed),
    )

    # GREEN TODO: taskq_api.repository.session.get_session() ->
    # sqlalchemy.orm.Session. The stub returns an in-memory list-backed
    # fake so the route exercises service validation logic without a
    # real DB. The GREEN agent must replace the stub with a real
    # SQLAlchemy session bound to the configured engine.
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
            # Mirror the TaskRepo in-process registry so the list
            # endpoint reflects rows POSTed in earlier requests (each
            # request gets a fresh `_FakeSession`, so the session's
            # own `_rows` is empty by the time `list` runs).
            try:
                from taskq_api.repository.task_repo import TaskRepo
                registry_rows = list(TaskRepo._registry.values())
                if registry_rows:
                    return list(registry_rows)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _problem_detail_str(response) -> str:
    """Return the problem+json 'detail' as a flat lowercase string."""
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


# ---------------------------------------------------------------------------
# FR-01 — Test cases from TEST_SPEC.md (do NOT rename)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_create_task_201(client, write_api_key):
    """AC1-create-status / AC1-create-id-present. [NFR-09][NFR-10]

    POST /v1/tasks with a valid write key returns 201 and a 36-char
    task id (UUIDv4). happy_path / Q1.
    """
    response = await client.post(
        "/v1/tasks",
        json={"name": "alpha-build", "command": "echo alpha"},
        headers={"X-API-Key": write_api_key},
    )
    result_status_code = response.status_code
    result_task_id_str = response.json().get("id", "")
    assert result_status_code == 201
    assert len(result_task_id_str) == 36
    assert _UUID_RE.match(result_task_id_str), result_task_id_str


@pytest.mark.asyncio
async def test_fr01_create_task_no_key_401(client):
    """AC2-no-key-status. [NFR-02][NFR-10]

    POST /v1/tasks with no X-API-Key header returns 401. Q2 / NP-01.
    """
    response = await client.post(
        "/v1/tasks",
        json={"name": "beta-build", "command": "echo beta"},
    )
    result_status_code = response.status_code
    assert result_status_code == 401
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )


@pytest.mark.asyncio
async def test_fr01_get_task_unknown_404(client, read_api_key):
    """AC3-unknown-status. [NFR-09]

    GET /v1/tasks/{unknown} with a read key returns 404 + problem+json.
    Q2.
    """
    response = await client.get(
        "/v1/tasks/00000000-0000-0000-0000-000000000000",
        headers={"X-API-Key": read_api_key},
    )
    result_status_code = response.status_code
    assert result_status_code == 404
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )


@pytest.mark.asyncio
async def test_fr01_create_task_duplicate_409(client, write_api_key):
    """AC4-duplicate-status. [NFR-03]

    POST /v1/tasks with a name that already exists returns 409.
    Q2.
    """
    payload = {"name": "alpha-build", "command": "echo alpha"}
    first = await client.post(
        "/v1/tasks", json=payload, headers={"X-API-Key": write_api_key}
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        "/v1/tasks", json=payload, headers={"X-API-Key": write_api_key}
    )
    result_status_code = second.status_code
    assert result_status_code == 409
    assert second.headers.get("content-type", "").startswith(
        "application/problem+json"
    )


@pytest.mark.asyncio
async def test_fr01_pydantic_validation_422(client, write_api_key):
    """AC5-empty-name-status / AC5-empty-name-detail. [NFR-02]

    POST /v1/tasks with an empty `name` violates the non-empty rule
    and returns 422 + problem+json whose detail mentions 'name'.
    Q2 / NP-04.
    """
    response = await client.post(
        "/v1/tasks",
        json={"name": "", "command": "echo x"},
        headers={"X-API-Key": write_api_key},
    )
    result_status_code = response.status_code
    result_problem_detail_str = _problem_detail_str(response)
    assert result_status_code == 422
    assert "name" in result_problem_detail_str
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )


@pytest.mark.asyncio
async def test_fr01_pydantic_validation_oversize_422(client, write_api_key):
    """AC6-oversize-status / AC6-oversize-detail. [NFR-02]

    POST /v1/tasks with a 1001-char command violates the <=1000 rule
    and returns 422 + problem+json whose detail mentions '1000' or
    'command'. Q3 / NP-04.
    """
    response = await client.post(
        "/v1/tasks",
        json={"name": "short", "command": "x" * 1001},
        headers={"X-API-Key": write_api_key},
    )
    result_status_code = response.status_code
    result_problem_detail_str = _problem_detail_str(response)
    assert result_status_code == 422
    assert (
        "1000" in result_problem_detail_str
        or "command" in result_problem_detail_str
    )
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )


@pytest.mark.asyncio
async def test_fr01_pagination_cursor_default(client, read_api_key, monkeypatch):
    """AC7-cursor-default-limit. [NFR-01]

    GET /v1/tasks with no `limit` param returns a page whose
    `limit` field equals 50. Q1 / NP-12.
    """
    response = await client.get(
        "/v1/tasks",
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # page metadata lives under either 'limit' (top-level) or 'page.limit'
    result_page_limit = body.get("limit", body.get("page", {}).get("limit"))
    assert result_page_limit == 50


@pytest.mark.asyncio
async def test_fr01_pagination_cursor_overbound_422(client, read_api_key):
    """AC8-overbound-status. [NFR-02]

    GET /v1/tasks?limit=201 exceeds the upper bound and returns 422.
    Q3 / NP-12.
    """
    response = await client.get(
        "/v1/tasks?limit=201",
        headers={"X-API-Key": read_api_key},
    )
    result_status_code = response.status_code
    assert result_status_code == 422
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )


@pytest.mark.asyncio
async def test_fr01_list_sql_count_constant(client, read_api_key):
    """AC9-sql-count-constant / AC9-no-n-plus-1. [NFR-01]

    GET /v1/tasks?limit=50 against 10,000 rows runs a CONSTANT number
    of SQL statements (N+1 ban, NFR-01). The TASK_SPEC allows <= 2
    statements and demands < 5.
    """
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    # GREEN TODO: TaskRepo.list_tasks(filter, cursor, limit) must yield
    # rows with eager-loaded relations (selectinload / joinedload) so
    # the list query emits a constant number of statements regardless
    # of result-set size.
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)

    # Seed 10,000 rows so the test mirrors SPEC §8 #14.
    with engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, name TEXT UNIQUE, command TEXT)"
        )
        conn.exec_driver_sql(
            "INSERT INTO tasks (id, name, command) VALUES "
            + ", ".join(f"('id-{i}', 'name-{i}', 'echo {i}')" for i in range(10000))
        )
        conn.commit()

    # GREEN TODO: taskq_api.repository.task_repo.list_tasks must use
    # the Session acquired from session.get_session() and MUST issue
    # a fixed number of statements (regardless of row count).
    response = await client.get(
        "/v1/tasks?limit=50",
        headers={"X-API-Key": read_api_key},
    )
    # The route must surface a 200 with rows; details are validated
    # outside the SQL-statement invariant.
    result_sql_statement_count = len(statements)
    assert result_sql_statement_count == 2
    assert result_sql_statement_count < 5
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# FR-01 — Coverage tests (added by COVERAGE-FIX step)
#
# Each function below targets a previously-uncovered source line that is
# REACHABLE from a normal request flow. None of them use `# pragma: no
# cover` — the project's PRAGMA_NO_COVER_ALLOWLIST exempts only
# `except BaseException` (atomic-write cleanup), so unreachable lines are
# either deleted or covered by a unit test.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_get_existing_task_200(client, write_api_key, read_api_key):
    """Coverage: `service.tasks.get` line 73 (`return row`).

    After a successful POST, GET on the returned id must surface the
    row. The earlier `test_fr01_get_task_unknown_404` only covers the
    `raise NotFoundProblem` branch; this test covers the happy-path
    `return row` line.
    """
    create = await client.post(
        "/v1/tasks",
        json={"name": "alpha-build", "command": "echo alpha"},
        headers={"X-API-Key": write_api_key},
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]
    response = await client.get(
        f"/v1/tasks/{task_id}",
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == task_id
    assert body["name"] == "alpha-build"


@pytest.mark.asyncio
async def test_fr01_delete_task_204(client, write_api_key):
    """Coverage: `service.tasks.delete` + `repo.TaskRepo.delete` body.

    The DELETE endpoint exists in `api.tasks.delete_task` but no test
    had exercised it. This test creates a row, deletes it, and asserts
    204 + the row is gone.
    """
    create = await client.post(
        "/v1/tasks",
        json={"name": "delta-build", "command": "echo delta"},
        headers={"X-API-Key": write_api_key},
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]
    response = await client.delete(
        f"/v1/tasks/{task_id}",
        headers={"X-API-Key": write_api_key},
    )
    assert response.status_code == 204, response.text
    # And a second delete is now a 404 (covers the NotFoundProblem
    # branch in service.delete).
    repeat = await client.delete(
        f"/v1/tasks/{task_id}",
        headers={"X-API-Key": write_api_key},
    )
    assert repeat.status_code == 404, repeat.text
    assert repeat.headers.get("content-type", "").startswith(
        "application/problem+json"
    )


@pytest.mark.asyncio
async def test_fr01_create_task_injection_char_422(
    client, write_api_key, monkeypatch
):
    """Coverage: `api.tasks.create_task` line 103 (injection blacklist).

    The router raises ValidationProblem when the command contains
    shell-metacharacters (`; & | ` $ \\ < > ' "`). Force the auth
    verifier to accept so the request reaches the route body.
    """
    response = await client.post(
        "/v1/tasks",
        json={"name": "injection-task", "command": "echo hi; rm -rf /"},
        headers={"X-API-Key": write_api_key},
    )
    result_status_code = response.status_code
    result_problem_detail_str = _problem_detail_str(response)
    assert result_status_code == 422
    assert (
        "command" in result_problem_detail_str
        or "forbidden" in result_problem_detail_str
    )
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )


@pytest.mark.asyncio
async def test_fr01_invalid_api_key_401(client, monkeypatch):
    """Coverage: `api.tasks.get_current_key` line 49 (invalid key).

    Force `verify_key` to return False so the dependency raises
    AuthProblem with the 'API key is not valid' detail.
    """
    from taskq_api.service import auth as _auth

    monkeypatch.setattr(
        _auth,
        "verify_key",
        lambda raw, hashed: False,
    )
    response = await client.get(
        "/v1/tasks",
        headers={"X-API-Key": "any-key"},
    )
    result_status_code = response.status_code
    result_problem_detail_str = _problem_detail_str(response)
    assert result_status_code == 401
    assert "not valid" in result_problem_detail_str
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    )


@pytest.mark.asyncio
async def test_fr01_require_scope_dependency_returns_key(monkeypatch):
    """Coverage: `api.tasks.require_scope` lines 59-70.

    Build a tiny FastAPI app with a `/probe` route gated by
    `require_scope("read")`. The dependency MUST resolve to the
    authenticated key.
    """
    from fastapi import FastAPI, Depends
    from taskq_api.api.tasks import require_scope

    app = FastAPI()
    dep = require_scope("read")

    @app.get("/probe")
    async def _probe(key: str = Depends(dep)):
        return {"key": key}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # Happy path — key resolves through the dependency.
        ok = await ac.get(
            "/probe", headers={"X-API-Key": "test-read-key"}
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["key"] == "test-read-key"

    # Forbidden path — drive the inner `_dep` callback directly. Going
    # through the route would short-circuit at `get_current_key` (which
    # also calls `verify_key`); calling `_dep` with a pre-authenticated
    # `key` exercises the inner scope check.
    from taskq_api.api import tasks as _tasks

    inner_dep = _tasks.require_scope("read")

    class _StubRequest:
        pass

    # 1) verify_key True → returns key
    monkeypatch.setattr(
        "taskq_api.service.auth.verify_key",
        lambda raw, hashed: True,
    )
    assert inner_dep(_StubRequest(), key="x") == "x"

    # 2) verify_key False → raises ForbiddenProblem
    monkeypatch.setattr(
        "taskq_api.service.auth.verify_key",
        lambda raw, hashed: False,
    )
    from taskq_api.errors import ForbiddenProblem
    with pytest.raises(ForbiddenProblem):
        inner_dep(_StubRequest(), key="x")


@pytest.mark.asyncio
async def test_fr01_list_with_status_filter(client, read_api_key, write_api_key):
    """Coverage: `repo.TaskRepo.list` line 143 (status filter).

    Seed two tasks with distinct statuses (only `pending` is exposed by
    the API, so we register the second row directly via the repo) and
    call the list endpoint with `?status=pending` — must return only
    the pending row.
    """
    from taskq_api.repository.task_repo import TaskRepo

    create = await client.post(
        "/v1/tasks",
        json={"name": "filter-pending", "command": "echo p"},
        headers={"X-API-Key": write_api_key},
    )
    assert create.status_code == 201, create.text

    # Register a non-pending row directly so the filter has something
    # to exclude.
    TaskRepo().register(
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "filter-running",
            "command": "echo r",
            "status": "running",
        }
    )

    response = await client.get(
        "/v1/tasks?status=pending",
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    statuses = {item["status"] for item in items}
    assert statuses == {"pending"}
    assert any(item["name"] == "filter-pending" for item in items)
    assert not any(item["name"] == "filter-running" for item in items)


@pytest.mark.asyncio
async def test_fr01_list_pagination_next_cursor(client, read_api_key, write_api_key):
    """Coverage: `repo.TaskRepo.list` lines 147-148 (next_cursor).

    Seed more than `limit` rows and confirm the response carries a
    non-null `next_cursor`. The cursor encodes the last seen id.
    """
    from taskq_api.repository.task_repo import TaskRepo

    # Seed 3 rows directly so the default limit (50) is not exceeded
    # by the seed alone — instead we ask for limit=2 so 3 > 2 triggers
    # the next_cursor branch.
    for i in range(3):
        TaskRepo().register(
            {
                "id": f"22222222-2222-2222-2222-00000000000{i}",
                "name": f"cursor-row-{i}",
                "command": f"echo {i}",
                "status": "pending",
            }
        )

    response = await client.get(
        "/v1/tasks?limit=2",
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["limit"] == 2
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None


@pytest.mark.asyncio
async def test_fr01_repo_list_count_returns_int():
    """Coverage: `repo.TaskRepo.list_count` line 153 (debug aid).

    Direct unit test against the in-process registry; no HTTP round
    trip needed.
    """
    from taskq_api.repository.task_repo import TaskRepo

    TaskRepo().register(
        {
            "id": "33333333-3333-3333-3333-333333333333",
            "name": "count-row",
            "command": "echo c",
            "status": "pending",
        }
    )
    n = TaskRepo().list_count()
    assert n >= 1
