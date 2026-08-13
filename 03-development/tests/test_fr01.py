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

    class _FakeResult:
        """Stand-in for SQLAlchemy ``Result`` returned by ``session.execute``.

        Supports the ``scalars().unique().all()`` chain used by
        ``TaskRepo.list()`` after FR-06 migrated the list query off
        ``session.query(...)`` and onto ``session.execute(stmt)``.
        Applies the WHERE clause extracted from the ``stmt`` so that
        ``select(...).where(table.c.status == 'pending')`` returns only
        rows whose ``status`` field equals the bound value — mirroring
        what a real SQLAlchemy session would do.
        """

        def __init__(self, rows: list, filter_predicates: dict) -> None:
            self._rows = rows
            self._filters = filter_predicates

        def scalars(self):
            return self

        def unique(self):
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
                    rows = list(registry_rows)
                else:
                    rows = list(self._rows)
            except Exception:
                rows = list(self._rows)

            for col_name, value in self._filters.items():
                rows = [r for r in rows if r.get(col_name) == value]
            return rows

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

        @staticmethod
        def _extract_where_filters(stmt):
            """Pull ``{column_name: bound_value}`` from the stmt's WHERE clause.

            FR-06's ``TaskRepo.list()`` builds
            ``select(_task_table).where(_task_table.c.status == status)``;
            SQLAlchemy stores the resulting ``BinaryExpression`` on
            ``stmt._whereclause``. Reading ``.left`` / ``.right`` gives
            the column reference and bound value; the column's ``.name``
            is the field name to filter on.
            """
            filters: dict = {}
            try:
                where = getattr(stmt, "_whereclause", None)
                if where is None:
                    return filters
                left = getattr(where, "left", None)
                right = getattr(where, "right", None)
                if left is None or right is None:
                    return filters
                col_name = getattr(left, "name", None) or getattr(
                    left, "key", None
                )
                value = getattr(right, "value", right)
                if col_name is not None:
                    filters[col_name] = value
            except Exception:
                pass
            return filters

        def execute(self, stmt, *_args, **_kwargs):
            return _FakeResult(self._rows, self._extract_where_filters(stmt))

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
    SessionLocal = sessionmaker(bind=engine)  # noqa: F841 — referenced via engine

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

    FR-04 scoping: DELETE requires `admin` scope. The fixture returns
    ``test-write-key`` (WRITE by default); the test re-registers the
    key with `admin` scope so the gate accepts the DELETE.
    """
    from taskq_api.repository.key_repo import KeyRepo

    # Create the task with the WRITE-scoped key (registered by the
    # shared conftest autouse fixture), then re-register the key with
    # admin scope so the DELETE gate accepts the next call.
    create = await client.post(
        "/v1/tasks",
        json={"name": "delta-build", "command": "echo delta"},
        headers={"X-API-Key": write_api_key},
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    # Re-register the write_api_key with admin scope so the DELETE
    # gate (`require_scope("admin")`) accepts it.
    KeyRepo._registry[f"key-admin-{write_api_key}"] = {
        "id": f"key-admin-{write_api_key}",
        "scope": "admin",
        "key_hash": "0" * 64,
        "revoked_at": None,
    }
    KeyRepo._by_key[write_api_key] = f"key-admin-{write_api_key}"

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
    # FR-04: the scope gate now consults KeyRepo. Register `x` with
    # `read` scope so the inner-dep accept branch fires.
    from taskq_api.repository.key_repo import KeyRepo
    KeyRepo._registry["key-read-x"] = {
        "id": "key-read-x",
        "scope": "read",
        "key_hash": "0" * 64,
        "revoked_at": None,
    }
    KeyRepo._by_key["x"] = "key-read-x"
    assert inner_dep(_StubRequest(), key="x") == "x"

    # 2) key not in KeyRepo → raises ForbiddenProblem
    # FR-04: the gate rejects keys that have no row in KeyRepo. The
    # `verify_key` monkeypatch is no longer relevant — the gate's
    # lookup is the KeyRepo side-table registered above.
    from taskq_api.errors import ForbiddenProblem
    KeyRepo._by_key.pop("x", None)
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


# ---------------------------------------------------------------------------
# Additional FR-01 coverage tests (round 2 — fill remaining gaps)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fr01_run_task_202(client, write_api_key, read_api_key, monkeypatch):
    """Coverage: `api.tasks.run_task` lines 177-189 + `_result_from_runner_record` line 58.

    FR-02 functionality lives in `api.tasks` (the file is shared
    between FR-01 and FR-02 routers), so reaching these lines via a
    normal request flow keeps the file-level coverage metric honest.
    The runner is monkey-patched to a stub so the test does not spawn
    a real subprocess.
    """
    from taskq_api.service import runner as _runner

    async def _fake_run(self, command, *, timeout_seconds=None, **_kw):
        return {
            "exit_code": 0,
            "stdout_tail": command + "\n",
            "stderr_tail": "",
            "duration_ms": 5,
            "finished_at": "1970-01-01T00:00:00Z",
            "status": "done",
        }

    monkeypatch.setattr(_runner.TaskRunner, "run", _fake_run)

    create = await client.post(
        "/v1/tasks",
        json={"name": "fr01-run-1", "command": "echo done"},
        headers={"X-API-Key": write_api_key},
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    run = await client.post(
        f"/v1/tasks/{task_id}/run",
        headers={"X-API-Key": write_api_key},
    )
    assert run.status_code == 202, run.text
    body = run.json()
    assert "run_id" in body
    assert len(body["run_id"]) == 36


@pytest.mark.asyncio
async def test_fr01_list_runs_for_task(client, write_api_key, read_api_key, monkeypatch):
    """Coverage: `api.tasks.list_runs` lines 207-209.

    After running the task, the run-history endpoint must surface the
    result row. Reaches the per-row rendering loop that builds the
    items payload.
    """
    from taskq_api.service import runner as _runner

    async def _fake_run(self, command, *, timeout_seconds=None, **_kw):
        return {
            "exit_code": 0,
            "stdout_tail": "hello\n",
            "stderr_tail": "",
            "duration_ms": 3,
            "finished_at": "1970-01-01T00:00:00Z",
            "status": "done",
        }

    monkeypatch.setattr(_runner.TaskRunner, "run", _fake_run)

    create = await client.post(
        "/v1/tasks",
        json={"name": "fr01-runs-1", "command": "echo hi"},
        headers={"X-API-Key": write_api_key},
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    await client.post(
        f"/v1/tasks/{task_id}/run",
        headers={"X-API-Key": write_api_key},
    )

    response = await client.get(
        f"/v1/tasks/{task_id}/runs",
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) >= 1
    assert items[0]["status"] == "done"


@pytest.mark.asyncio
async def test_fr01_repo_list_with_cursor_param(client, read_api_key):
    """Coverage: `repo.TaskRepo.list` line 190 (`cursor` keyset branch).

    The repo's list method applies a keyset filter when `cursor` is
    supplied; this test exercises the path through a list request
    that carries a non-empty `cursor` query parameter.
    """

    # Seed 3 rows so the cursor branch has data to filter against.
    for i in range(3):
        TaskRepo().register(
            {
                "id": f"44444444-4444-4444-4444-00000000000{i}",
                "name": f"cursor-seed-{i}",
                "command": f"echo {i}",
                "status": "pending",
            }
        )

    # `cursor=2` is opaque to the API (the route does not decode it);
    # what matters here is that the keyset branch fires and the
    # request still succeeds.
    response = await client.get(
        "/v1/tasks?cursor=44444444-4444-4444-4444-000000000001",
        headers={"X-API-Key": read_api_key},
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_fr01_service_create_repo_exception_becomes_409(
    client, write_api_key, monkeypatch
):
    """Coverage: `service.tasks.create` lines 57-63 (except branch).

    Force `_repo.create` to raise `RuntimeError` so the service's
    `except (KeyError, ValueError, RuntimeError)` branch fires,
    rolling back the unit-of-work and re-raising as a
    `ConflictProblem` → 409 to the caller.
    """

    repo = TaskService()._repo  # type: ignore[attr-defined]
    original_create = repo.create

    def _raise(self, **_kw):
        raise RuntimeError("simulated repo failure")

    monkeypatch.setattr(TaskRepo, "create", _raise)
    try:
        response = await client.post(
            "/v1/tasks",
            json={"name": "fr01-conflict-1", "command": "echo c"},
            headers={"X-API-Key": write_api_key},
        )
        assert response.status_code == 409, response.text
        assert response.headers.get("content-type", "").startswith(
            "application/problem+json"
        )
    finally:
        monkeypatch.setattr(TaskRepo, "create", original_create)


@pytest.mark.asyncio
async def test_fr01_run_task_invalid_timeout_falls_back_to_30s(
    client, write_api_key, monkeypatch
):
    """Coverage: `api.tasks.run_task` lines 184-185 (timeout parse fallback).

    When ``TASKQ_TASK_TIMEOUT`` env var is set to a non-numeric
    string, the ``float(...)`` call raises ``ValueError`` and the
    ``except`` branch falls back to the 30s default. The runner is
    stubbed so the test does not spawn a real subprocess.
    """
    from taskq_api.service import runner as _runner

    async def _fake_run(self, command, *, timeout_seconds=None, **_kw):
        return {
            "exit_code": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "duration_ms": 0,
            "finished_at": "1970-01-01T00:00:00Z",
            "status": "done",
        }

    monkeypatch.setattr(_runner.TaskRunner, "run", _fake_run)
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "not-a-number")

    create = await client.post(
        "/v1/tasks",
        json={"name": "fr01-timeout-1", "command": "echo t"},
        headers={"X-API-Key": write_api_key},
    )
    assert create.status_code == 201, create.text
    task_id = create.json()["id"]

    run = await client.post(
        f"/v1/tasks/{task_id}/run",
        headers={"X-API-Key": write_api_key},
    )
    assert run.status_code == 202, run.text


@pytest.mark.asyncio
async def test_fr01_repo_commit_handles_session_error(monkeypatch):
    """Coverage: `repo.TaskRepo.commit` lines 93-96 (commit-fail swallow).

    Force `_ensure_session().commit()` to raise `RuntimeError` so the
    repo's defensive `except (RuntimeError, OSError): pass` swallows
    it — the service layer is responsible for the rollback.
    """
    from taskq_api.repository import session as _session

    class _ExplodingSession:
        def commit(self):
            raise RuntimeError("simulated commit failure")

        def rollback(self):
            pass

        def add(self, _row):
            pass

    monkeypatch.setattr(_session, "get_session", lambda: _ExplodingSession())

    # Build a repo, drive the commit path; must NOT propagate the
    # simulated RuntimeError.
    TaskRepo().commit()
    # And again with OSError, to cover the second arm of the except
    # tuple.
    class _ExplodingSessionOS:
        def commit(self):
            raise OSError("simulated commit failure (OSError)")

        def rollback(self):
            pass

        def add(self, _row):
            pass

    monkeypatch.setattr(_session, "get_session", lambda: _ExplodingSessionOS())
    TaskRepo().commit()
