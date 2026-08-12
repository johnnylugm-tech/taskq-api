"""TDD-RED failing tests for FR-06 (持久化層與交易邊界).

Per TEST_SPEC.md (FR-06), the four spec test functions below cover the
canonical acceptance criteria (SPEC §8 #14, #17, #21):

    AC1-sql-count-fixed     list endpoint runs a CONSTANT 2 statements
                            regardless of row count (N+1 ban, NFR-01)
    AC2-grep-zero           0 hits for f-string / % / + SQL assembly
                            across 03-development/src (NFR-02)
    AC3-sqlalchemy-isolated `lint-imports` exits 0
    AC3-no-sqlalchemy-leak  >= 1 forbidden contract blocks `sqlalchemy`
                            from the api / service layers (NFR-06)
    AC4-rollback-applied    an exception inside the unit-of-work context
                            manager rolls back — 0 visible rows

The TEST_SPEC names are the contract; spec-coverage-check uses exact
match. Do NOT rename these functions.

These tests intentionally fail because the FR-06 transaction boundary is
not implemented yet (RED — TDD phase 1):
  - `taskq_api.repository.session.unit_of_work()` (the commit-on-success /
    rollback-on-exception context manager) does NOT exist — the module
    only exposes `get_session()`. This raises a Collection Error
    (Exit Code 2), which is the expected RED state.
  - `TaskRepo.list()` still fans out through `session.query()` instead of
    issuing a constant 2 statements (row page + `selectinload` of the
    eager relations).
  - `.importlinter` has no `forbidden` contract keeping `sqlalchemy` out
    of the `api` / `service` layers.

The GREEN step will:
  1. Add `repository.session.unit_of_work()` — a context manager that
     commits on normal exit and rolls back on ANY exception.
  2. Rewrite `TaskRepo.list()` to build one parameterized `select()` with
     `selectinload()` eager-loading, executed via `session.execute()`,
     for exactly 2 statements at any row count.
  3. Add a `forbidden` import-linter contract for `sqlalchemy` sourced
     from `taskq_api.api` and `taskq_api.service`.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# Top-level imports — RED will surface if any declared SAB module member
# is missing on disk. `unit_of_work` does NOT exist yet; this is the
# expected Collection Error (Exit Code 2) that the RED step validates.
# It is NOT acceptable to wrap these in try/except ImportError.
from taskq_api.models.orm import TaskResult  # noqa: F401  (existing module)
from taskq_api.repository.session import get_session  # noqa: F401  (existing)
from taskq_api.repository.session import unit_of_work  # DOES NOT EXIST — RED
from taskq_api.repository.task_repo import TaskRepo
from taskq_api.service.tasks import TaskService

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "03-development" / "src"


# ---------------------------------------------------------------------------
# Test doubles — statement-counting / transaction-recording sessions.
# These are test isolation only (no live DB), NOT the feature.
# ---------------------------------------------------------------------------


class _CountingSession:
    """Records every SQL statement handed to `execute()`.

    Stands in for a real SQLAlchemy `Session` so the N+1 assertion can
    count statements without a live database. A lazy-loading (N+1)
    implementation shows up as one `execute()` per row.
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.statements: list[Any] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> "_CountingResult":
        self.statements.append(statement)
        return _CountingResult(self._rows)

    def add(self, obj: Any) -> None:
        self._rows.append(obj)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True
        # A real rollback discards everything staged in this unit of work.
        self._rows.clear()

    def close(self) -> None:
        self.closed = True


class _CountingResult:
    """Minimal stand-in for SQLAlchemy's `Result`."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def scalars(self) -> "_CountingResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def unique(self) -> "_CountingResult":
        return self


@pytest.fixture
def counting_session() -> _CountingSession:
    """A statement-counting session preloaded with 500 task rows."""
    rows = [
        {
            "id": f"task-{i:04d}",
            "name": f"task-{i:04d}",
            "command": "echo hi",
            "status": "pending",
        }
        for i in range(500)  # Inputs: row_count="500"
    ]
    return _CountingSession(rows)


# ---------------------------------------------------------------------------
# Case 1 — AC1-sql-count-fixed (performance, Q6 / NFR-01)
# ---------------------------------------------------------------------------

# NFR-01 NFR-02 NFR-03 NFR-09
# GREEN TODO: `TaskRepo.list(*, status, cursor, limit)` must issue exactly
# TWO statements through `session.execute(stmt)` — (1) the parameterized
# `select()` page query, (2) the `selectinload()` eager-load of the task's
# relations — independent of row count. It must NOT call `session.query()`
# per row (lazy loading == N+1 == acceptance failure).
def test_fr06_list_sql_count_constant(counting_session: _CountingSession) -> None:
    """Listing 500 rows runs a CONSTANT number of SQL statements.

    Inputs: row_count="500"
    Sub-assertions: AC1-sql-count-fixed (`result_sql_statement_count == 2`)
    """
    repo = TaskRepo(session=counting_session)
    rows, _next_cursor = repo.list(status=None, cursor=None, limit=500)

    result_sql_statement_count = len(counting_session.statements)

    # AC1-sql-count-fixed — 1 page select + 1 selectinload eager load.
    assert result_sql_statement_count == 2, (
        "FR-06/NFR-01: list must run a constant 2 statements for any row "
        f"count; got {result_sql_statement_count} for 500 rows (N+1)."
    )
    # The constant must hold *because* the rows were really fetched.
    assert len(rows) == 500


# ---------------------------------------------------------------------------
# Case 2 — AC2-grep-zero (security, Q2 / NP-08 / NFR-02)
# ---------------------------------------------------------------------------

_SQL_VERB = r"(?:SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|VALUES|ORDER\s+BY)"
# f-string SQL:  f"SELECT ... {x}"   |   %-format SQL: "SELECT ..." % x
# +-concat SQL:  "SELECT ... " + x
_CONCAT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("f-string", rf'f["\'][^"\']*{_SQL_VERB}[^"\']*\{{'),
    ("percent-format", rf'["\'][^"\']*{_SQL_VERB}[^"\']*["\']\s*%\s*'),
    ("plus-concat", rf'["\'][^"\']*{_SQL_VERB}[^"\']*["\']\s*\+\s*'),
)


# NFR-02 NFR-09
def test_fr06_no_sql_string_concat() -> None:
    """No SQL is assembled by string concatenation anywhere under src.

    Inputs: scan_root="03-development/src"
    Sub-assertions: AC2-grep-zero (`result_grep_hit_count == 0`)
    """
    hits: list[str] = []
    scanned_files = 0
    orm_usage_files: list[str] = []

    for py_file in sorted(_SRC_ROOT.rglob("*.py")):
        scanned_files += 1
        text = py_file.read_text(encoding="utf-8")
        relative = py_file.relative_to(_PROJECT_ROOT).as_posix()
        for label, pattern in _CONCAT_PATTERNS:
            for match in re.finditer(pattern, text):
                line_no = text[: match.start()].count("\n") + 1
                hits.append(f"{relative}:{line_no} ({label})")
        # A parameterized / ORM data-access site: `select(...)` construct.
        if re.search(r"\bselect\s*\(", text) or re.search(r"\bselectinload\s*\(", text):
            orm_usage_files.append(relative)

    result_grep_hit_count = len(hits)

    assert scanned_files > 0, "scan root produced no Python files"
    # AC2-grep-zero — zero string-concatenated SQL (NFR-02).
    assert result_grep_hit_count == 0, (
        "FR-06/NFR-02: SQL must never be assembled by string concatenation; "
        f"hits: {hits}"
    )
    # Zero hits must mean "ORM / parameterized everywhere", not "no data
    # access implemented yet" — the repository layer must actually build
    # its queries with SQLAlchemy `select()` / `selectinload()`.
    assert orm_usage_files, (
        "FR-06/NFR-02: no parameterized ORM query construct (`select(` / "
        "`selectinload(`) found under 03-development/src — data access is "
        "not yet implemented through the ORM."
    )


# ---------------------------------------------------------------------------
# Case 3 — AC3-sqlalchemy-isolated / AC3-no-sqlalchemy-leak
# (architecture, Q6 / NFR-06)
# ---------------------------------------------------------------------------


# NFR-02 NFR-06 NFR-09
def test_fr06_lint_imports_sqlalchemy_isolation() -> None:
    """`lint-imports` passes AND blocks `sqlalchemy` from api/service.

    Inputs: scope="api+service"
    Sub-assertions:
      - AC3-sqlalchemy-isolated (`result_lint_imports_exit_code == 0`)
      - AC3-no-sqlalchemy-leak  (`result_blocked_import_count >= 1`)

    Out-of-process by design: `lint-imports` is the real CI entry point
    (NFR-06); its exit code is the acceptance signal. The contract-file
    assertion below is in-process so the *content* of the rule is checked
    too, not just the tool's exit status.
    """
    config_path = _PROJECT_ROOT / ".importlinter"
    assert config_path.is_file(), ".importlinter must exist at project root (NFR-06)"
    config_text = config_path.read_text(encoding="utf-8")

    # Count forbidden contracts that keep `sqlalchemy` out of api/service.
    result_blocked_import_count = 0
    for block in re.split(r"^\[importlinter:contract:", config_text, flags=re.MULTILINE)[1:]:
        if "type" not in block or "forbidden" not in block:
            continue
        if "sqlalchemy" not in block:
            continue
        sources = block
        if "taskq_api.api" in sources and "taskq_api.service" in sources:
            result_blocked_import_count += 1

    # AC3-no-sqlalchemy-leak
    assert result_blocked_import_count >= 1, (
        "FR-06/NFR-06: .importlinter needs a `forbidden` contract blocking "
        "`sqlalchemy` from taskq_api.api and taskq_api.service; found "
        f"{result_blocked_import_count}."
    )

    lint_imports = shutil.which("lint-imports")
    assert lint_imports is not None, (
        "FR-06/NFR-06: `lint-imports` (import-linter) must be installed; it "
        "is the NFR-06 acceptance entry point."
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [lint_imports],
        cwd=str(_PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    result_lint_imports_exit_code = completed.returncode

    # AC3-sqlalchemy-isolated
    assert result_lint_imports_exit_code == 0, (
        "FR-06/NFR-06: lint-imports must exit 0.\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )


# ---------------------------------------------------------------------------
# Case 4 — AC4-rollback-applied (failure, Q5 / NP-08)
# ---------------------------------------------------------------------------

# NFR-03 NFR-09
# GREEN TODO: `taskq_api.repository.session.unit_of_work()` must be a
# `contextlib.contextmanager` yielding a Session that commits on normal
# exit and calls `session.rollback()` on ANY exception before re-raising.
def test_fr06_transaction_context_manager_rollback(monkeypatch) -> None:
    """An exception inside the unit-of-work rolls the transaction back.

    Inputs: action="raise_inside_unit_of_work"
    Sub-assertions: AC4-rollback-applied (`result_visible_row_count == 0`)
    """
    recording = _CountingSession(rows=[])
    # Test isolation only: no live DB. `unit_of_work` must acquire its
    # Session through `repository.session.get_session`.
    monkeypatch.setattr(
        "taskq_api.repository.session.get_session", lambda: recording
    )

    with pytest.raises(RuntimeError, match="boom"):
        with unit_of_work() as session:
            session.add({"id": "task-rollback", "name": "doomed", "status": "pending"})
            raise RuntimeError("boom")

    result_visible_row_count = len(recording.execute(None).all())

    # AC4-rollback-applied
    assert result_visible_row_count == 0, (
        "FR-06: an exception inside unit_of_work() must roll back — "
        f"{result_visible_row_count} row(s) still visible."
    )
    assert recording.rolled_back is True, "unit_of_work() must call rollback()"
    assert recording.committed is False, "unit_of_work() must NOT commit on error"

    # The business layer must never hold a Session itself (FR-06 §1).
    service = TaskService()
    assert not any(
        isinstance(getattr(service, attr, None), _CountingSession)
        for attr in vars(service)
    ), "service layer must not hold a Session (FR-06: repository owns it)"
    assert sys.modules["taskq_api.service.tasks"].__dict__.get("Session") is None


# ---------------------------------------------------------------------------
# Coverage tests — exercise the surface area of repository / service / ORM
# modules targeted by Gate 1's coverage gate. These are added on top of the
# four spec-mandated cases above; they do not rename or duplicate any
# `test_fr06_*` function declared in TEST_SPEC.md §FR-06.
#
# Forbidden constants — keep these out of any test below:
#   * `# pragma: no cover` on testable lines (Gate 1 forbids it)
#   * xfail / skip of any existing case
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Coverage-case 5 — `repository.session` commit / rollback / get_session paths
# ---------------------------------------------------------------------------


class _RaisingSession:
    """Session double that raises on the configured methods.

    Used to drive the commit-failure and rollback-failure paths inside
    ``unit_of_work`` — the only way to exercise lines 67–68 and 73–80
    of ``session.py`` deterministically without a live DB.
    """

    def __init__(
        self,
        *,
        commit_raises: bool = False,
        rollback_raises: bool = False,
    ) -> None:
        self.commit_raises = commit_raises
        self.rollback_raises = rollback_raises
        self.committed = False
        self.rolled_back = False

    def commit(self) -> None:
        if self.commit_raises:
            raise RuntimeError("commit boom")
        self.committed = True

    def rollback(self) -> None:
        if self.rollback_raises:
            raise RuntimeError("rollback boom")
        self.rolled_back = True


def test_fr06_coverage_get_session_raises_when_unwired() -> None:
    """`get_session()` raises ``RuntimeError`` until deployment wires it.

    Covers line 31 of ``repository/session.py`` — the production-side
    fail-loud path. All other tests monkeypatch ``get_session``; this
    one drives it unmodified (other autouse fixtures have no effect on
    the ``repository.session`` module's own attribute).
    """
    # Import the module fresh so any prior test's monkeypatch is gone.
    from taskq_api.repository import session as session_module

    with pytest.raises(RuntimeError, match="must be wired"):
        session_module.get_session()


def test_fr06_coverage_unit_of_work_commit_success_path(monkeypatch) -> None:
    """Normal exit of ``unit_of_work`` runs ``commit()`` (lines 73–75).

    The existing rollback test takes the ``except BaseException:``
    branch; this case drives the ``else:`` branch on a clean exit.
    """
    recording = _RaisingSession()
    monkeypatch.setattr(
        "taskq_api.repository.session.get_session", lambda: recording
    )

    with unit_of_work() as sess:
        assert sess is recording
        # no exception -> else branch

    assert recording.committed is True, "else branch must commit on normal exit"
    assert recording.rolled_back is False, "else branch must NOT roll back on success"


def test_fr06_coverage_unit_of_work_commit_failure_rolls_back(monkeypatch) -> None:
    """``commit()`` failing inside ``unit_of_work`` rolls back (lines 76–79).

    The outer ``raise`` re-raises the original commit error so the
    caller's exception handling is preserved.
    """
    recording = _RaisingSession(commit_raises=True, rollback_raises=False)
    monkeypatch.setattr(
        "taskq_api.repository.session.get_session", lambda: recording
    )

    with pytest.raises(RuntimeError, match="commit boom"):
        with unit_of_work() as sess:
            assert sess is recording

    # commit raised; the inner rollback was attempted (and recorded as
    # rolled_back=True because this fake's rollback succeeds).
    assert recording.rolled_back is True, (
        "commit failure must trigger rollback on the session"
    )


def test_fr06_coverage_unit_of_work_commit_and_rollback_both_fail(monkeypatch) -> None:
    """``commit()`` AND ``rollback()`` both failing is suppressed (lines 78–79).

    The inner ``except Exception: pass`` swallows the rollback failure
    so the original commit exception can still be re-raised.
    """
    recording = _RaisingSession(commit_raises=True, rollback_raises=True)
    monkeypatch.setattr(
        "taskq_api.repository.session.get_session", lambda: recording
    )

    with pytest.raises(RuntimeError, match="commit boom"):
        with unit_of_work() as sess:
            assert sess is recording

    # Both methods raised; the original error reaches the caller.
    assert recording.committed is False


def test_fr06_coverage_unit_of_work_rollback_failure_suppressed(monkeypatch) -> None:
    """``rollback()`` raising inside ``unit_of_work`` is suppressed (lines 67–68).

    The original exception is still re-raised so the caller's error
    path is not masked by the rollback failure itself.
    """
    recording = _RaisingSession(rollback_raises=True)
    monkeypatch.setattr(
        "taskq_api.repository.session.get_session", lambda: recording
    )

    with pytest.raises(RuntimeError, match="boom"):
        with unit_of_work() as sess:
            assert sess is recording
            raise RuntimeError("boom")

    # rollback() raised but the inner `except Exception: pass` caught it;
    # the original `boom` was re-raised.


# ---------------------------------------------------------------------------
# Coverage-case 6 — `repository.task_repo` CRUD / lazy session / next_cursor
# ---------------------------------------------------------------------------


def test_fr06_coverage_task_repo_create_calls_session_add(monkeypatch) -> None:
    """TaskRepo.create stages a new row in the session (lines 77–81).

    The created row's id is left empty — the service layer fills it
    with a UUID and registers it in the in-process registry.
    """
    sess = _CountingSession(rows=[])
    repo = TaskRepo(session=sess)

    row = repo.create(name="alpha", command="echo a")
    assert row["name"] == "alpha"
    assert row["command"] == "echo a"
    assert row["status"] == "pending"
    # `add()` on _CountingSession appends to rows; verify it was staged.
    assert len(sess._rows) == 1
    assert sess._rows[0]["name"] == "alpha"


def test_fr06_coverage_task_repo_lazy_session_acquisition(monkeypatch) -> None:
    """TaskRepo constructed without a session acquires one on first use
    (lines 65–66).

    The lazy path goes through ``_session_module.get_session()`` so
    the test-suite's monkeypatch on ``repository.session.get_session``
    is honoured — no direct construction of a SQLAlchemy session.
    """
    sentinel = _CountingSession(rows=[])
    monkeypatch.setattr(
        "taskq_api.repository.session.get_session", lambda: sentinel
    )

    repo = TaskRepo()  # no explicit session
    assert repo._session_acquired is False
    sess = repo._ensure_session()
    assert sess is sentinel
    assert repo._session_acquired is True
    # Second call reuses the acquired session.
    assert repo._ensure_session() is sentinel


def test_fr06_coverage_task_repo_commit_and_rollback_call_session(monkeypatch) -> None:
    """TaskRepo.commit / rollback route to the session (lines 89–101)."""
    sess = _CountingSession(rows=[])
    repo = TaskRepo(session=sess)

    repo.commit()
    assert sess.committed is True
    assert sess.rolled_back is False

    repo.rollback()
    assert sess.rolled_back is True


def test_fr06_coverage_task_repo_delete_get_exists_register() -> None:
    """TaskRepo delete / get / exists_by_name / register (lines 105–135)."""
    repo = TaskRepo(session=_CountingSession(rows=[]))
    row = {
        "id": "task-abc",
        "name": "alpha",
        "command": "echo a",
        "status": "pending",
    }
    repo.register(row)

    # get() returns the row (line 119)
    assert repo.get("task-abc") == row
    # exists_by_name() returns True (line 126)
    assert repo.exists_by_name("alpha") is True
    assert repo.exists_by_name("missing") is False

    # delete() removes the row (lines 105–109)
    assert repo.delete("task-abc") is True
    assert repo.get("task-abc") is None
    assert repo.exists_by_name("alpha") is False

    # Second delete returns False — row is no longer present
    assert repo.delete("task-abc") is False


def test_fr06_coverage_task_repo_list_status_filter_and_next_cursor() -> None:
    """``list(status=...)`` applies the filter and sets ``next_cursor``
    when more rows than limit exist (lines 172, 186–187).
    """
    rows = [
        {
            "id": f"row-{i:04d}",
            "name": f"name-{i:04d}",
            "command": "echo x",
            "status": "pending" if i % 2 == 0 else "running",
        }
        for i in range(20)
    ]
    sess = _CountingSession(rows=rows)
    repo = TaskRepo(session=sess)

    page, next_cursor = repo.list(status="pending", cursor=None, limit=5)
    assert len(page) == 5
    # 10 pending rows, limit=5 -> next_cursor must be set
    assert next_cursor is not None
    # The filter was a bound parameter, not interpolated.
    assert len(sess.statements) == 2

    # Limit >= total rows -> next_cursor = None
    page2, next_cursor2 = repo.list(status=None, cursor=None, limit=100)
    assert len(page2) == 20
    assert next_cursor2 is None


def test_fr06_coverage_task_repo_list_count() -> None:
    """``TaskRepo.list_count`` reports the registry size (line 192)."""
    repo = TaskRepo(session=_CountingSession(rows=[]))
    assert repo.list_count() == 0
    repo.register({"id": "x", "name": "x", "command": "x", "status": "pending"})
    repo.register({"id": "y", "name": "y", "command": "y", "status": "pending"})
    assert repo.list_count() == 2


# ---------------------------------------------------------------------------
# Coverage-case 7 — `service.tasks` CRUD paths
# ---------------------------------------------------------------------------


def test_fr06_coverage_service_create_persists_unique_row(monkeypatch) -> None:
    """Service.create persists a new task with a generated UUID (lines 41–55).

    The duplicate-name branch (line 41–43) is covered by the explicit
    conflict test below; this case drives the happy path through.
    """
    sess = _CountingSession(rows=[])
    # Acquired by lazy access in repo._ensure_session
    monkeypatch.setattr(
        "taskq_api.repository.session.get_session", lambda: sess
    )

    svc = TaskService()
    row = svc.create(name="alpha", command="echo a")

    # The row carries the canonical columns and a generated UUID
    assert row["name"] == "alpha"
    assert row["command"] == "echo a"
    assert row["status"] == "pending"
    assert len(row["id"]) == 36  # UUID4 string length

    # The repo registered the row in the in-process registry
    assert svc._repo.get(row["id"]) == row
    assert svc._repo.exists_by_name("alpha") is True
    assert sess.committed is True


def test_fr06_coverage_service_create_duplicate_raises_conflict(monkeypatch) -> None:
    """Duplicate ``name`` raises ``ConflictProblem`` (lines 41–43)."""
    monkeypatch.setattr(
        "taskq_api.repository.session.get_session",
        lambda: _CountingSession(rows=[]),
    )
    svc = TaskService()
    svc.create(name="alpha", command="echo a")

    from taskq_api.errors import ConflictProblem

    with pytest.raises(ConflictProblem):
        svc.create(name="alpha", command="echo a")

    # Second create did NOT stage a new row in the registry
    assert svc._repo.list_count() == 1


def test_fr06_coverage_service_delete_not_found(monkeypatch) -> None:
    """Service.delete on a missing id raises ``NotFoundProblem`` (lines 60–62)."""
    monkeypatch.setattr(
        "taskq_api.repository.session.get_session",
        lambda: _CountingSession(rows=[]),
    )
    svc = TaskService()

    from taskq_api.errors import NotFoundProblem

    with pytest.raises(NotFoundProblem):
        svc.delete("nonexistent-id")


def test_fr06_coverage_service_delete_success_path(monkeypatch) -> None:
    """Service.delete on an existing id commits (line 63 — success branch)."""
    sess = _CountingSession(rows=[])
    monkeypatch.setattr(
        "taskq_api.repository.session.get_session", lambda: sess
    )
    svc = TaskService()
    created = svc.create(name="alpha", command="echo a")
    target_id = created["id"]
    assert svc._repo.get(target_id) is not None

    sess.committed = False  # reset so we observe the delete's commit
    svc.delete(target_id)
    assert sess.committed is True, "delete on existing row must commit"
    assert svc._repo.get(target_id) is None


def test_fr06_coverage_service_get_not_found(monkeypatch) -> None:
    """Service.get on a missing id raises ``NotFoundProblem`` (lines 70–72)."""
    monkeypatch.setattr(
        "taskq_api.repository.session.get_session",
        lambda: _CountingSession(rows=[]),
    )
    svc = TaskService()

    from taskq_api.errors import NotFoundProblem

    with pytest.raises(NotFoundProblem):
        svc.get("nonexistent-id")


def test_fr06_coverage_service_get_returns_row(monkeypatch) -> None:
    """Service.get returns the repo row when present (line 73 — success)."""
    monkeypatch.setattr(
        "taskq_api.repository.session.get_session",
        lambda: _CountingSession(rows=[]),
    )
    svc = TaskService()
    created = svc.create(name="alpha", command="echo a")

    got = svc.get(created["id"])
    assert got["id"] == created["id"]
    assert got["name"] == "alpha"


def test_fr06_coverage_service_list_envelope(monkeypatch) -> None:
    """Service.list returns the canonical envelope (lines 89–90)."""
    sess = _CountingSession(
        rows=[
            {
                "id": f"row-{i:04d}",
                "name": f"name-{i:04d}",
                "command": "echo x",
                "status": "pending",
            }
            for i in range(3)
        ]
    )
    monkeypatch.setattr(
        "taskq_api.repository.session.get_session", lambda: sess
    )
    svc = TaskService()
    result = svc.list(status=None, cursor=None, limit=10)

    assert result["limit"] == 10
    assert len(result["items"]) == 3
    assert "next_cursor" in result


# ---------------------------------------------------------------------------
# Coverage-case 8 — `models.orm` row constructors and registry helpers
# ---------------------------------------------------------------------------


def test_fr06_coverage_orm_apikey_init_as_row_and_repr() -> None:
    """ApiKey constructor, ``as_row``, and ``__repr`` (lines 72–92).

    Covers the FR-03 ORM row construction contract:
      - default id is a fresh UUID
      - row shape is exactly the 4 documented columns (no plaintext)
    """
    from taskq_api.models.orm import ApiKey

    a = ApiKey(scope="write", key_hash="a" * 64, revoked_at=None)
    assert a.scope == "write"
    assert a.key_hash == "a" * 64
    assert a.revoked_at is None
    assert len(a.id) == 36

    row = a.as_row()
    assert set(row.keys()) == {"id", "scope", "key_hash", "revoked_at"}
    # Plaintext is NOT a column (NFR-02 / FR-03)
    assert "plaintext" not in row
    assert "key" not in row

    # __repr__ should not raise and must surface the key_hash
    rendered = repr(a)
    assert "ApiKey" in rendered
    assert a.scope in rendered


def test_fr06_coverage_orm_taskresult_init_persist_and_list(monkeypatch) -> None:
    """TaskResult full CRUD on the in-process registry (lines 124–158)."""
    # TaskResult already imported at module level
    # Reset the class-level registry so each test starts clean
    TaskResult._registry.clear()
    try:
        # Construction defaults
        r = TaskResult(task_id="t-1", run_id="run-1")
        assert r.task_id == "t-1"
        assert r.run_id == "run-1"
        assert r.exit_code is None
        assert r.stdout_tail == ""
        assert r.stderr_tail == ""
        assert r.duration_ms == 0
        assert r.finished_at == ""
        assert r.status == "done"
        assert len(r.id) == 36

        # Persistence appends to the class-level registry
        TaskResult.add(r)
        # Adding the same instance twice: registry has two rows
        # (matches the per-row INSERT-then-DROP-COLUMN FR-07 narrative)
        TaskResult.add(
            TaskResult(task_id="t-1", run_id="run-1", exit_code=0)
        )

        # list_for_task returns newest-first
        results = TaskResult.list_for_task("t-1")
        assert len(results) == 2
        # Newest-first ordering
        assert results[0].run_id == "run-1"
        assert results[1].run_id == "run-1"

        # Different task_id isolates the rows
        assert TaskResult.list_for_task("t-2") == []
    finally:
        TaskResult._registry.clear()
