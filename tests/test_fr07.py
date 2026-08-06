"""RED acceptance tests for FR-07 Schema migration (Alembic three-step evolution).

[FR-07]
Citations: SPEC.md §3 FR-07 (AC-7.1..AC-7.5); SRS.md §3 FR-07;
            SAD.md §2.3.10, §3.6.

The five FR-07 test cases map 1:1 to the canonical five AC bullets:

  - AC-7.1 (upgrade head + downgrade base exit zero) -> case 1
  - AC-7.2 (round-trip preserves every column)       -> case 2
  - AC-7.3 (no destructive downgrade shortcut)       -> case 3
  - AC-7.4 (offline SQL generation)                  -> case 4
  - AC-7.5 (real SQLite file, NFR-09 anti-skip)      -> case 5

IN-PROCESS vs OUT-OF-PROCESS (explicit choice, per the integration
guidelines):

  * Case 1 runs the real ``alembic`` CLI via ``subprocess`` because the
    acceptance criterion IS the CLI exit code (SPEC §8 #13). Coverage of
    the migration modules is not measurable through a subprocess.
  * Cases 2, 4 and 5 drive alembic IN-PROCESS through
    ``alembic.command.{upgrade,downgrade}`` so pytest-cov actually
    measures ``migrations.versions.*`` — the subprocess coverage ceiling
    would otherwise leave the SAB-declared modules at 0% and block
    Gate 1's ``test_coverage`` dimension.
  * Case 3 is a static source scan; it needs neither.

Top-level imports are deliberate. ``alembic`` (declared in
``.methodology/env_contract.json``) and the three SAB-declared migration
modules do not exist yet, so collection fails with ``ModuleNotFoundError``
(pytest exit code 2). Per the task contract that IS the valid RED state,
not a defect to mask with ``try``/``except ImportError``.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

# GREEN TODO: ``migrations.versions.v1_initial``, ``migrations.versions.v2_tags``
# and ``migrations.versions.v3_split_results`` are the SAB-declared dotted paths
# for FR-07 (`.methodology/SAB.json` -> fr_module_traceability["FR-07"], and
# SAD.md §2.3.10). GREEN must materialise them under
# ``03-development/src/migrations/versions/`` — plus ``migrations/__init__.py``
# and ``migrations/versions/__init__.py`` so the dotted import below resolves,
# and ``migrations/env.py`` + a project-root ``alembic.ini`` whose
# ``script_location`` points at ``03-development/src/migrations``.
#
# Each module must expose the standard Alembic revision surface:
#     revision: str, down_revision: str | None, upgrade() -> None, downgrade() -> None
#
#   v1_initial        — create ``tasks``, ``api_keys`` (and ``rate_buckets``
#                       per SAD.md §2.3.10); downgrade drops them.
#   v2_tags           — add ``tags`` / ``task_tags`` (many-to-many) plus a
#                       unique index on ``tasks.name``; downgrade drops the new
#                       tables and the index without touching v1 data.
#   v3_split_results  — data-moving: create ``task_results(task_id, result_json)``,
#                       copy every ``tasks.result_json`` value into it, then
#                       drop the ``tasks.result_json`` column. downgrade must
#                       re-add ``tasks.result_json``, copy the values BACK, and
#                       only then drop ``task_results`` (no data loss, AC-7.2).
#
# ``migrations/env.py`` must honour the ``TASKQ_DATABASE_URL`` environment
# variable (same name ``taskq_api.repository.session`` already reads) so the
# CLI case below can point alembic at a throwaway database.
from migrations.versions import v1_initial, v2_tags, v3_split_results

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "03-development" / "src"
MIGRATIONS_DIR = SRC_ROOT / "migrations"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

_ALEMBIC_INI_MISSING = (
    f"alembic.ini missing at {ALEMBIC_INI} — GREEN must materialise the "
    "Alembic configuration as part of the FR-07 migration surface "
    "(SAD.md §2.3.10)."
)


# ---------------------------------------------------------------------------
# Test support helpers (test isolation only — NOT the feature under test)
# ---------------------------------------------------------------------------


def _alembic_config(db_path: Path, output_buffer: io.StringIO | None = None) -> Config:
    """Build an Alembic ``Config`` pinned to a throwaway SQLite file.

    ``script_location`` is forced to the SAB-declared migrations directory so
    the test does not depend on the process working directory, and
    ``sqlalchemy.url`` is overridden so no test ever touches the project's
    canonical ``taskq.db``.
    """
    assert ALEMBIC_INI.exists(), _ALEMBIC_INI_MISSING
    config = Config(str(ALEMBIC_INI), output_buffer=output_buffer)
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open the real on-disk SQLite file with row access by column name."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    """Return every user table present in the database."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row["name"] for row in rows}


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return the column names of ``table`` (empty list if it does not exist)."""
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _placeholder_for(col_name: str, col_type: str) -> object:
    """Synthesise a type-compatible filler for a NOT NULL column."""
    declared = (col_type or "").upper()
    if "INT" in declared:
        return 0
    if any(token in declared for token in ("REAL", "FLOA", "DOUB", "NUMERIC", "DEC")):
        return 0.0
    if "BLOB" in declared:
        return b"fr07"
    if "DATE" in declared or "TIME" in declared:
        return "2026-08-06T00:00:00"
    return f"fr07-{col_name}"


def _insert_row(conn: sqlite3.Connection, table: str, values: dict[str, object]) -> None:
    """Insert a row into ``table``, auto-filling NOT NULL columns not supplied.

    The FR-07 ACs constrain only ``result_json``'s journey between ``tasks``
    and ``task_results``; the remaining ``tasks`` columns are FR-01's business.
    Filling them by introspection keeps this test coupled to the FR-07
    contract alone instead of to FR-01's exact column list.
    """
    table_info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    assert table_info, (
        f"table {table!r} does not exist — the migration did not create it"
    )
    payload: dict[str, object] = {}
    for row in table_info:
        col_name, col_type, not_null, default_value = row[1], row[2], row[3], row[4]
        if col_name in values:
            payload[col_name] = values[col_name]
        elif not_null and default_value is None:
            payload[col_name] = _placeholder_for(col_name, col_type)
    columns = ", ".join(payload)
    markers = ", ".join("?" * len(payload))
    conn.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({markers})", tuple(payload.values())
    )
    conn.commit()


def _row_as_dict(
    conn: sqlite3.Connection, table: str, key_column: str, key_value: object
) -> dict[str, object]:
    """Snapshot a single row as a plain dict so full-row equality is assertable."""
    row = conn.execute(
        f"SELECT * FROM {table} WHERE {key_column} = ?", (key_value,)
    ).fetchone()
    assert row is not None, (
        f"row {key_value!r} vanished from {table!r} — the migration lost data"
    )
    return dict(row)


def _child_env(db_path: Path) -> dict[str, str]:
    """Environment for an out-of-process alembic run.

    ``os.environ.copy()`` is mandatory: a bare dict strips ``PATH`` / ``HOME``
    and the child would fail for reasons unrelated to FR-07. pytest's
    ``sys.path`` wiring (root ``conftest.py``) does NOT propagate to children,
    so ``PYTHONPATH`` is set explicitly.
    """
    env = os.environ.copy()
    env["TASKQ_DATABASE_URL"] = f"sqlite:///{db_path}"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_ROOT), str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return env


# ---------------------------------------------------------------------------
# FR-07 / AC-7.1 — alembic upgrade head and alembic downgrade base succeed
# ---------------------------------------------------------------------------


def test_fr07_upgrade_head_then_downgrade_base_exit_zero(tmp_path: Path) -> None:
    """AC-7.1: ``alembic upgrade head`` and ``alembic downgrade base`` exit 0.

    OUT-OF-PROCESS by design — the acceptance criterion is literally the
    exit code of the alembic CLI (SPEC §3 FR-07, §8 #13), so the real
    console entry point is what gets exercised.

    # NFR-03 — migrations.env must wire alembic's transaction boundary so the
    # whole upgrade/downgrade succeeds or rolls back atomically (TRACEABILITY
    # §5.1 line 414: `migrations.env` carries FR-07 + NFR-03).
    """
    target_head = "head"
    target_base = "base"
    assert target_head == "head"  # AC7.1-upgrade-target
    assert target_base == "base"  # AC7.1-downgrade-target

    # The three revisions must form a single linear chain base -> v1 -> v2 -> v3
    # or neither "head" nor "base" resolves to a unique revision.
    assert v1_initial.down_revision is None, (
        "v1_initial must be the base revision (down_revision is None)"
    )
    assert v2_tags.down_revision == v1_initial.revision, (
        "v2_tags must chain onto v1_initial"
    )
    assert v3_split_results.down_revision == v2_tags.revision, (
        "v3_split_results must chain onto v2_tags"
    )

    assert ALEMBIC_INI.exists(), _ALEMBIC_INI_MISSING

    db_path = tmp_path / "ac71.db"
    env = _child_env(db_path)
    base_argv = [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI)]

    upgrade = subprocess.run(
        [*base_argv, "upgrade", target_head],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert upgrade.returncode == 0, (
        f"`alembic upgrade {target_head}` exited {upgrade.returncode}\n"
        f"stdout:\n{upgrade.stdout}\nstderr:\n{upgrade.stderr}"
    )

    downgrade = subprocess.run(
        [*base_argv, "downgrade", target_base],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )
    assert downgrade.returncode == 0, (
        f"`alembic downgrade {target_base}` exited {downgrade.returncode}\n"
        f"stdout:\n{downgrade.stdout}\nstderr:\n{downgrade.stderr}"
    )

    # SPEC §8 #13: "downgrade base -> exit 0, no leftover tables".
    conn = _connect(db_path)
    try:
        leftover = _table_names(conn) - {"alembic_version", "sqlite_sequence"}
    finally:
        conn.close()
    assert leftover == set(), (
        f"`alembic downgrade base` left tables behind: {sorted(leftover)}"
    )


# ---------------------------------------------------------------------------
# FR-07 / AC-7.2 — round-trip preserves every column value byte-identical
# ---------------------------------------------------------------------------


def test_fr07_v3_round_trip_preserves_every_column(tmp_path: Path) -> None:
    """AC-7.2: upgrade head -> write data -> downgrade -1 -> upgrade head loses nothing.

    IN-PROCESS (``alembic.command``) so pytest-cov measures the v3 data move.
    The whole point of this AC is that the v3 downgrade REVERSE-MIGRATES rows
    out of ``task_results`` back into ``tasks.result_json`` before dropping the
    table; a ``DROP TABLE`` shortcut fails here with lost data.

    # NFR-09 — v3 round-trip is the canonical "real SQLite file" evidence
    # required by AC-09.5 (TRACEABILITY §5.1 line 417: `migrations.versions
    # .v3_split_results` carries FR-07 + NFR-09).
    """
    sample_value = "round-trip-fixture"
    column_name = "result_json"
    assert sample_value != ""  # AC7.2-sample-shape

    db_path = tmp_path / "ac72.db"
    config = _alembic_config(db_path)

    # --- head (v3): tasks has NO result_json; task_results holds the payload.
    command.upgrade(config, "head")

    conn = _connect(db_path)
    try:
        tables = _table_names(conn)
        assert "tasks" in tables, "v1 must create the `tasks` table"
        assert "task_results" in tables, (
            "v3 must create the `task_results` table (SPEC §3 FR-07)"
        )
        assert column_name not in _column_names(conn, "tasks"), (
            f"v3 must DROP the original `tasks.{column_name}` column after "
            "moving its data into `task_results`"
        )
        # GREEN TODO: v3_split_results must name the new table's columns
        # `task_id` (FK -> tasks.id) and `result_json` — the reverse migration
        # in downgrade() reads exactly these.
        result_columns = _column_names(conn, "task_results")
        assert "task_id" in result_columns and column_name in result_columns, (
            f"task_results must expose (task_id, {column_name}); got {result_columns}"
        )

        _insert_row(conn, "tasks", {"id": "row-1", "name": "task-A"})
        _insert_row(
            conn, "task_results", {"task_id": "row-1", column_name: sample_value}
        )
        before = _row_as_dict(conn, "tasks", "id", "row-1")
    finally:
        conn.close()

    # --- downgrade -1 (v3 -> v2): payload must be back on tasks.result_json.
    command.downgrade(config, "-1")

    conn = _connect(db_path)
    try:
        assert "task_results" not in _table_names(conn), (
            "v3 downgrade must drop `task_results` after reverse-migrating"
        )
        merged = _row_as_dict(conn, "tasks", "id", "row-1")
        assert merged[column_name] == sample_value, (
            f"v3 downgrade lost the payload: tasks.{column_name} is "
            f"{merged[column_name]!r}, expected {sample_value!r}"
        )
    finally:
        conn.close()

    # --- upgrade head again (v2 -> v3): re-split, byte-identical.
    command.upgrade(config, "head")

    conn = _connect(db_path)
    try:
        after = _row_as_dict(conn, "tasks", "id", "row-1")
        recovered = _row_as_dict(conn, "task_results", "task_id", "row-1")[column_name]
    finally:
        conn.close()

    assert after == before, (
        "round-trip changed non-result columns of `tasks`:\n"
        f"  before={before}\n  after ={after}"
    )
    assert recovered == sample_value  # P7-roundtrip-bytes-equal


# ---------------------------------------------------------------------------
# FR-07 / AC-7.3 — destructive shortcuts (raw DROP TABLE) are forbidden
# ---------------------------------------------------------------------------


def test_fr07_downgrade_has_no_destructive_shortcut() -> None:
    """AC-7.3: a raw ``op.execute("DROP TABLE ...")`` may not stand in for a downgrade.

    Every revision must implement ``downgrade()`` with Alembic's reversible
    operations (``op.drop_table`` / ``op.drop_index`` / ``op.add_column``).
    Scanned statically against each module's own source file, resolved via
    ``inspect`` so both SAB-permitted on-disk shapes (leaf module or package)
    are covered.
    """
    forbidden_pattern = "op.execute(DROP TABLE"
    assert forbidden_pattern != ""  # AC7.3-no-dropshortcut

    # The spec input above is the human-readable shape of the shortcut. Real
    # source spells it with a quote, an f-string prefix, or a sa.text() wrapper,
    # so the scan matches every spelling of the same shortcut.
    shortcut_re = re.compile(
        r"""op\.execute\s*\(\s*        # op.execute(
            (?:sa\.text\s*\(\s*)?      # optional sa.text( wrapper
            [furbFURB]*                # optional f/r/u/b string prefix
            ["']\s*DROP\s+TABLE        # the destructive DDL itself
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    for module in (v1_initial, v2_tags, v3_split_results):
        module_name = module.__name__
        source_file = inspect.getsourcefile(module)
        assert source_file is not None, f"cannot locate source of {module_name}"
        source_path = Path(source_file)
        assert source_path.is_file(), f"{module_name} has no source file on disk"

        # A revision with no real downgrade is the same defect wearing a
        # different hat, so assert the callable exists and has a body.
        assert callable(getattr(module, "downgrade", None)), (
            f"{module_name} must define a downgrade() callable (AC-7.1/AC-7.3)"
        )
        downgrade_source = inspect.getsource(module.downgrade)
        downgrade_body = re.sub(r'""".*?"""', "", downgrade_source, flags=re.DOTALL)
        statements = [
            line.strip()
            for line in downgrade_body.splitlines()[1:]
            if line.strip() and not line.strip().startswith("#")
        ]
        assert statements not in ([], ["pass"], ["..."]), (
            f"{module_name}.downgrade() is a stub; AC-7.3 forbids substituting "
            "a no-op or a destructive shortcut for a real downgrade"
        )

        # Strip docstrings/comments so prose ABOUT the shortcut (including the
        # GREEN TODO wording) cannot false-positive the scan.
        scrubbed = re.sub(
            r'""".*?"""|\'\'\'.*?\'\'\'',
            "",
            source_path.read_text(encoding="utf-8"),
            flags=re.DOTALL,
        )
        scrubbed = re.sub(r"(?m)#.*$", "", scrubbed)
        offenders = shortcut_re.findall(scrubbed)
        assert not offenders, (
            f"{source_path.name} contains a destructive downgrade shortcut "
            f"({forbidden_pattern!r}); AC-7.3 requires Alembic reversible "
            "operations (op.drop_table, op.drop_index)."
        )


# ---------------------------------------------------------------------------
# FR-07 / AC-7.4 — offline SQL generation produces the expected DDL
# ---------------------------------------------------------------------------


def test_fr07_offline_sql_generation_matches_expected(tmp_path: Path) -> None:
    """AC-7.4: ``alembic upgrade head --sql`` emits the expected DDL.

    IN-PROCESS ``command.upgrade(..., sql=True)`` — offline mode needs no live
    connection, and running it here (rather than through the CLI) keeps the
    migration modules inside pytest-cov's measurement scope.
    """
    expected_table_name = "tasks"
    assert expected_table_name == "tasks"  # AC7.4-table-shaped

    # GREEN TODO: the v3 data move must be authored as bulk SQL
    # (`op.execute(sa.text("INSERT INTO task_results ... SELECT ..."))`) rather
    # than a Python row loop over `op.get_bind()`. Offline mode has no
    # connection to iterate, so a row loop makes this AC unsatisfiable.
    buffer = io.StringIO()
    captured = io.StringIO()
    db_path = tmp_path / "ac74.db"
    with contextlib.redirect_stdout(captured):
        config = _alembic_config(db_path, output_buffer=buffer)
        command.upgrade(config, "head", sql=True)

    # Alembic writes offline DDL to the config's output buffer, falling back to
    # stdout; accept either so the assertion tests the DDL, not the plumbing.
    ddl = buffer.getvalue() + captured.getvalue()
    assert ddl.strip(), "offline SQL generation produced no output at all"

    assert f"CREATE TABLE {expected_table_name}" in ddl, (
        f"offline SQL did not emit CREATE TABLE for {expected_table_name!r}\n"
        f"full DDL:\n{ddl}"
    )
    assert "CREATE TABLE task_results" in ddl, (
        "offline SQL for `head` must include the v3 `task_results` split\n"
        f"full DDL:\n{ddl}"
    )
    # Offline generation must not have touched a real database.
    assert not db_path.exists(), (
        "`--sql` (offline) mode must not open or create a database file"
    )


# ---------------------------------------------------------------------------
# FR-07 / AC-7.5 — real-database-file migration round-trip (NFR-09 anti-skip)
# ---------------------------------------------------------------------------


def test_fr07_data_move_verified_on_real_sqlite_file(tmp_path: Path) -> None:
    """AC-7.5: the data move is verified against a real SQLite file, never a mock.

    NFR-09 forbids downgrading this to a skip on "migration logic is hard to
    test" grounds, and forbids substituting an in-memory database. The file is
    created on disk, closed between every migration step, and re-opened — so a
    ``sqlite:///:memory:`` implementation cannot pass.

    # NFR-09 — sister assertion to AC-7.2 (same v3 data move); both together
    # fulfil AC-09.5 "real SQLite file, per-column round-trip, no skip".
    """
    sqlite_filename = "roundtrip.db"
    sample_value = "real-fixture"
    assert sqlite_filename != ""  # AC7.5-real-file-shape

    db_path = tmp_path / sqlite_filename
    assert not db_path.exists(), "fixture DB must start absent"

    config = _alembic_config(db_path)
    command.upgrade(config, "head")

    # A real file, on a real filesystem — the NFR-09 anti-mock guarantee.
    assert db_path.is_file(), (
        f"{sqlite_filename} was not created on disk — the migration ran "
        "against an in-memory database, which AC-7.5 forbids"
    )
    assert db_path.stat().st_size > 0, f"{sqlite_filename} is empty on disk"

    conn = _connect(db_path)
    try:
        _insert_row(conn, "tasks", {"id": "row-1", "name": "task-A"})
        _insert_row(
            conn, "task_results", {"task_id": "row-1", "result_json": sample_value}
        )
    finally:
        conn.close()

    command.downgrade(config, "-1")

    conn = _connect(db_path)
    try:
        reversed_value = _row_as_dict(conn, "tasks", "id", "row-1")["result_json"]
    finally:
        conn.close()
    assert reversed_value == sample_value, (
        "the v3 downgrade did not reverse-migrate the payload back into "
        f"tasks.result_json (got {reversed_value!r}, expected {sample_value!r})"
    )

    command.upgrade(config, "head")

    conn = _connect(db_path)
    try:
        recovered = _row_as_dict(conn, "task_results", "task_id", "row-1")[
            "result_json"
        ]
    finally:
        conn.close()
    assert recovered == sample_value, (
        f"real-file round-trip on {sqlite_filename} lost the payload "
        f"(got {recovered!r}, expected {sample_value!r})"
    )
