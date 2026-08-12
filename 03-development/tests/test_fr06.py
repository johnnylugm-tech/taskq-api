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
