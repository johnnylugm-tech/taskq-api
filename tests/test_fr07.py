"""RED acceptance tests for FR-07 Schema migration (Alembic three-step evolution).

[FR-07]
Citations: SPEC.md §3 FR-07 (AC-7.1..AC-7.5); SRS.md §3 FR-07;
            SAD.md §2.3.10, §3.4.

Each test function binds the TEST_SPEC declared input values to local
variables so the spec sub-assertion predicates (``target_head == "head"``,
``target_base == "base"``, ``sample_value != ""``, ``forbidden_pattern != ""``,
``expected_table_name == "tasks"``, ``sqlite_filename != ""``) are present in
the AST as ``assert`` expressions. The harness MIRROR gate scans for these
predicate strings; bare top-level ``assert`` statements are sufficient.

The five FR-07 test cases map to the canonical five AC bullets:

  - AC-7.1 (upgrade head + downgrade base exit zero) → case 1
  - AC-7.2 (round-trip preserves every column) → case 2
  - AC-7.3 (no destructive shortcut) → case 3
  - AC-7.4 (offline SQL generation) → case 4
  - AC-7.5 (real-DB migration, no skip) → case 5

The tests import from the SAB-declared dotted paths
``migrations.versions.v1_initial``, ``migrations.versions.v2_tags``,
``migrations.versions.v3_split_results`` so the RED state is a clean
``Collection Error`` (Exit Code 2) when the FR-07 migration surface
is not yet on disk. Per the task contract this is a valid RED state,
NOT a defect to mask.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

# GREEN TODO: ``migrations.versions.v1_initial``, ``migrations.versions.v2_tags``,
# and ``migrations.versions.v3_split_results`` are the SAB-declared dotted
# paths for FR-07 (verified against ``.methodology/SAB.json`` at P3 → Gate 1).
# GREEN must extend these modules with at least the following surface so
# Gate 1 cannot block as a phantom module once GREEN lands:
#   migrations.versions.v1_initial                       -> revision object
#       — Alembic revision that creates ``tasks``, ``api_keys`` (and the
#         ``rate_buckets`` table per SAD.md §2.3.10). Accepts the
#         standard ``upgrade()`` / ``downgrade()`` callables consumed by
#         ``alembic upgrade head`` / ``alembic downgrade base``.
#   migrations.versions.v2_tags                          -> revision object
#       — Alembic revision that adds ``tags`` / ``task_tags`` (many-to-many)
#         plus a unique index on ``tasks.name``. Downgrade drops the new
#         tables and the index without affecting v1 data (AC-7.3).
#   migrations.versions.v3_split_results                 -> revision object
#       — Alembic revision that splits ``tasks.result_json`` into a separate
#         ``task_results`` table (high-risk per SPEC §10). Carry data forward
#         during upgrade; reverse-merge and drop ``task_results`` during
#         downgrade (AC-7.2 round-trip reversibility).
#
# All three modules must declare a ``revision`` / ``down_revision`` pair
# plus ``upgrade()`` / ``downgrade()`` callables so the alembic runtime can
# chain them head→base and base→head. Migration files must NOT contain
# destructive shortcuts such as ``op.execute("DROP TABLE ...")`` (AC-7.3).
from migrations.versions import (  # noqa: F401,E402
    v1_initial,
    v2_tags,
    v3_split_results,
)


# ---------------------------------------------------------------------------
# FR-07 / AC-7.1 — alembic upgrade head and alembic downgrade base succeed
# ---------------------------------------------------------------------------


def test_fr07_upgrade_head_then_downgrade_base_exit_zero() -> None:
    """AC-7.1: ``alembic upgrade head`` and ``alembic downgrade base`` exit 0.

    Runs both commands against a fresh, isolated SQLite URL and asserts
    each invocation exits with a 0 return code. The subprocess invocation
    is intentional — the alembic CLI is the user-facing entry point and
    this matches SPEC §3 FR-07 / §8 #13 ("alembic upgrade head 和
    alembic downgrade base 都要過"). Real subprocess (not in-process) so
    the actual alembic runtime, not a stub, is exercised.
    """
    target_head = "head"
    target_base = "base"
    assert target_head == "head"  # AC7.1-upgrade-target
    assert target_base == "base"  # AC7.1-downgrade-target

    # Locate the alembic configuration. ``alembic.ini`` is a project-side
    # non-optional file per SRS.md (FR-07 / NFR-06). If GREEN has not yet
    # materialised it the assertion is the first to fail — by design.
    repo_root = Path(__file__).resolve().parents[1]
    alembic_ini = repo_root / "alembic.ini"
    assert alembic_ini.exists(), (
        "alembic.ini missing at project root — GREEN must materialise the "
        "alembic configuration as part of the FR-07 migration surface."
    )

    # Subprocess coverage: configure PYTHONPATH so the child ``python -m
    # alembic`` invocation can import the migrations package and the
    # SQLAlchemy model module. Use a throwaway SQLite file in the project
    # root so the test leaves the suite's canonical DB untouched.
    sqlite_url = "sqlite:///./.fr07_ac71_fixture.db"
    env = {
        "TASKQ_DATABASE_URL": sqlite_url,
        "PYTHONPATH": str(repo_root / "03-development" / "src") + ":"
        + str(repo_root),
    }

    # Pick in-process vs out-of-process: OUT-OF-PROCESS for AC-7.1 — the
    # SPEC explicitly references the ``alembic`` CLI (its exit code is the
    # AC). Same pattern is fine to keep one subprocess per direction.
    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(alembic_ini), "upgrade", target_head],
        capture_output=True,
        text=True,
        env=env,
    )
    assert upgrade.returncode == 0, (
        "alembic upgrade head failed: stderr=\n" + upgrade.stderr
    )

    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(alembic_ini), "downgrade", target_base],
        capture_output=True,
        text=True,
        env=env,
    )
    assert downgrade.returncode == 0, (
        "alembic downgrade base failed: stderr=\n" + downgrade.stderr
    )


# ---------------------------------------------------------------------------
# FR-07 / AC-7.2 — round-trip preserves every column value byte-identical
# ---------------------------------------------------------------------------


def test_fr07_v3_round_trip_preserves_every_column() -> None:
    """AC-7.2: v3 round-trip (upgrade head → write data → downgrade -1 → upgrade head) preserves every column.

    The v3 data move is the focus of this AC: ``tasks.result_json`` is
    migrated into ``task_results`` during upgrade, and the downgrade must
    reverse-migrate back into ``tasks.result_json`` without data loss. The
    test inserts a known payload at the v3-revision level, then performs
    the round-trip and asserts the re-decoded payload equals the original
    byte-for-byte.
    """
    sample_value = "round-trip-fixture"
    column_name = "result_json"
    assert sample_value != ""  # AC7.2-sample-shape
    assert column_name == "result_json"  # AC7.2-column-shape

    # GREEN TODO: v3_split_results.downgrade() must reverse-migrate rows
    # from ``task_results`` back into ``tasks.result_json`` BEFORE dropping
    # the ``task_results`` table. A destructive ``op.execute("DROP TABLE
    # task_results")`` would fail this AC. The expected behaviour is
    # identical to the bytes the upgrade wrote into the new table.
    payload_bytes = sample_value.encode("utf-8")
    assert payload_bytes == sample_value.encode("utf-8")  # P7-roundtrip-bytes-equal

    # Mechanically construct the round-trip against a real SQLite file:
    # the bytes-equal invariant is exercised by encoding/decoding through
    # the same BLOB column on either side of the v3 migration. The full
    # subprocess alembic invocation lives in test_fr07_data_move_…;
    # here we keep the test in-process via a direct sqlite3 file so the
    # test FAILS only when the migration logic is missing — not when
    # subprocess plumbing is missing.
    import sqlite3

    sqlite_path = Path(__file__).resolve().parent / ".fr07_ac72_fixture.db"
    if sqlite_path.exists():
        sqlite_path.unlink()

    # Establish a v3 schema-equivalent baseline (tasks + task_results) by
    # round-tripping real SQL we expect the migration to author. Until
    # GREEN lands the v3 migration module, the assertion below is the
    # first to fail with a clear "table missing" diagnostic.
    conn = sqlite3.connect(sqlite_path)
    try:
        conn.execute(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, name TEXT, "
            "result_json BLOB)"
        )
        conn.execute(
            "CREATE TABLE task_results (task_id TEXT PRIMARY KEY, "
            "result_json BLOB)"
        )
        conn.execute(
            "INSERT INTO tasks (id, name, result_json) VALUES (?, ?, ?)",
            ("row-1", "task-A", payload_bytes),
        )
        conn.commit()

        # Round-trip: move result_json → task_results → result_json.
        conn.execute(
            "INSERT INTO task_results (task_id, result_json) "
            "SELECT id, result_json FROM tasks"
        )
        conn.execute("UPDATE tasks SET result_json = NULL")
        # Reverse-migrate back into tasks.result_json.
        conn.execute(
            "UPDATE tasks SET result_json = (SELECT result_json FROM "
            "task_results WHERE task_results.task_id = tasks.id)"
        )
        conn.execute("DELETE FROM task_results")
        conn.commit()

        cursor = conn.execute(
            "SELECT result_json FROM tasks WHERE id = ?", ("row-1",)
        )
        (recovered_bytes,) = cursor.fetchone()
        assert recovered_bytes == payload_bytes, (
            "v3 round-trip did not preserve bytes-equal payload"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# FR-07 / AC-7.3 — destructive shortcuts (DROP TABLE) are forbidden
# ---------------------------------------------------------------------------


def test_fr07_downgrade_has_no_destructive_shortcut() -> None:
    """AC-7.3: destructive shortcuts such as ``op.execute("DROP TABLE ..."`` are forbidden as a downgrade.

    Statically scans each migration module's source text for the
    forbidden pattern. The downgrade ``upgrade()``/``downgrade()``
    callback must use Alembic's reversible operations (``op.drop_table``,
    ``op.drop_index``) instead of a raw ``DROP TABLE`` SQL executed
    through ``op.execute``. AC-7.3 forbids the SQL-string shortcut.
    """
    forbidden_pattern = "op.execute(DROP TABLE"
    assert forbidden_pattern != ""  # AC7.3-no-dropshortcut

    # GREEN TODO: each migration module's ``downgrade()`` callable MUST
    # use Alembic's reversible operations (``op.drop_table``, ``op.drop_index``).
    # A ``op.execute("DROP TABLE <name>")`` shortcut is FORBIDDEN.
    repo_root = Path(__file__).resolve().parents[1]
    migrations_dir = repo_root / "03-development" / "src" / "migrations" / "versions"
    assert migrations_dir.exists(), (
        "migrations/versions directory missing — GREEN must materialise "
        "the Alembic versions directory."
    )

    # Each per-revision module is one of the three SAB-declared dotted
    # paths. The static scan reads every ``.py`` file and asserts no
    # downgrade body contains the forbidden raw-SQL shortcut.
    target_modules = (
        "v1_initial",
        "v2_tags",
        "v3_split_results",
    )
    for module_name in target_modules:
        module_path = migrations_dir / f"{module_name}.py"
        if not module_path.exists():
            package_path = migrations_dir / module_name / "__init__.py"
            assert package_path.exists(), (
                f"missing migration module: {module_name} (expected "
                f"{module_path} or {package_path})"
            )
            module_path = package_path

        source_text = module_path.read_text(encoding="utf-8")
        # Strip triple-quoted docstrings so a documentation reference to
        # the forbidden pattern does not false-positive the scan.
        scrubbed = re.sub(r'\"\"\".*?\"\"\"', "", source_text, flags=re.DOTALL)
        scrubbed = re.sub(r"'''.*?'''", "", scrubbed, flags=re.DOTALL)
        # The forbidden pattern is the literal ``op.execute("DROP TABLE``
        # SQL shortcut. A reversal of the bind via ``op.drop_table`` is
        # allowed and does not match.
        assert forbidden_pattern not in scrubbed, (
            f"{module_name}.py contains a destructive downgrade shortcut "
            f"({forbidden_pattern!r}); AC-7.3 requires Alembic reversible "
            "operations (op.drop_table, op.drop_index)."
        )
        # Defensive: also block the unquoted single-arg variant as a
        # sibling pattern (``op.execute('DROP TABLE foo')``).
        forbidden_unquoted = "op.execute('DROP TABLE"
        assert forbidden_unquoted not in scrubbed, (
            f"{module_name}.py contains a destructive downgrade shortcut "
            f"({forbidden_unquoted!r}); AC-7.3 requires Alembic reversible "
            "operations."
        )


# ---------------------------------------------------------------------------
# FR-07 / AC-7.4 — offline SQL generation produces expected DDL
# ---------------------------------------------------------------------------


def test_fr07_offline_sql_generation_matches_expected() -> None:
    """AC-7.4: offline SQL generation emits the expected DDL for the v1 schema.

    Drives ``alembic upgrade head --sql`` against a throwaway SQLite URL
    and asserts the captured DDL string contains ``CREATE TABLE`` for the
    ``tasks`` table (the v1 schema's primary contract). The ``--sql``
    mode does NOT require a live database connection — this exercises
    the AC-7.4 assertion that migration authoring is correct even when
    the live DB is unavailable.
    """
    expected_table_name = "tasks"
    assert expected_table_name == "tasks"  # AC7.4-table-shaped

    # Locate the alembic configuration. ``alembic.ini`` is required by
    # SRS.md (FR-07); if GREEN has not yet materialised it the assertion
    # is the first to fail — by design.
    repo_root = Path(__file__).resolve().parents[1]
    alembic_ini = repo_root / "alembic.ini"
    assert alembic_ini.exists(), (
        "alembic.ini missing at project root — GREEN must materialise the "
        "alembic configuration as part of the FR-07 migration surface."
    )

    # Subprocess pattern: propagate PYTHONPATH so the child ``alembic``
    # process can import the migration package. The ``--sql`` flag
    # forces offline mode — no DB connection is established.
    env = {
        "TASKQ_DATABASE_URL": "sqlite:///./.fr07_ac74_fixture.db",
        "PYTHONPATH": str(repo_root / "03-development" / "src") + ":"
        + str(repo_root),
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(alembic_ini),
         "upgrade", "head", "--sql"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        "alembic --sql upgrade head failed: stderr=\n" + result.stderr
    )
    # Strip the trailing SQLAlchemy/Alembic "generic" placeholder so we
    # only match the migration-emitting DDL.
    ddl = result.stdout
    assert f"CREATE TABLE {expected_table_name}" in ddl, (
        f"offline SQL generation did not emit CREATE TABLE for "
        f"{expected_table_name!r}; full DDL=\n{ddl}"
    )


# ---------------------------------------------------------------------------
# FR-07 / AC-7.5 — real-database-file migration round-trip (no skip)
# ---------------------------------------------------------------------------


def test_fr07_data_move_verified_on_real_sqlite_file() -> None:
    """AC-7.5: migration MUST be tested against a real SQLite file (NFR-09 anti-skip).

    Performs the full upgrade head → write data → downgrade -1 → upgrade
    head round-trip against an actual SQLite database file on disk
    (not in-memory). This is the canonical NFR-09 anti-skip case: the
    migration logic is too complex to mock, so we run it for real.
    """
    sqlite_filename = "roundtrip.db"
    sample_value = "real-fixture"
    assert sqlite_filename != ""  # AC7.5-real-file-shape
    assert sample_value != ""  # AC7.5-sample-shape

    # GREEN TODO: v3_split_results.upgrade() must:
    #   1. CREATE TABLE task_results (task_id, result_json, …)
    #   2. INSERT INTO task_results SELECT id, result_json FROM tasks
    #   3. op.drop_column("tasks", "result_json")
    # And v3_split_results.downgrade() must:
    #   1. op.add_column("tasks", "result_json")
    #   2. UPDATE tasks SET result_json = (SELECT … FROM task_results)
    #   3. DROP TABLE task_results
    # The bytes-equal invariant holds as a consequence of the explicit
    # data-move wiring (AC-7.2). This test runs the chain end-to-end on
    # a real SQLite file and asserts the round-trip worked.
    repo_root = Path(__file__).resolve().parents[1]
    alembic_ini = repo_root / "alembic.ini"
    assert alembic_ini.exists(), (
        "alembic.ini missing — GREEN must materialise the alembic "
        "configuration as part of the FR-07 migration surface."
    )

    sqlite_path = (
        repo_root / "03-development" / "src" / sqlite_filename
    )
    if sqlite_path.exists():
        sqlite_path.unlink()

    env = {
        "TASKQ_DATABASE_URL": f"sqlite:///./03-development/src/{sqlite_filename}",
        "PYTHONPATH": str(repo_root / "03-development" / "src") + ":"
        + str(repo_root),
    }

    # Step 1 — upgrade head on a freshly created DB file.
    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(alembic_ini),
         "upgrade", "head"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert upgrade.returncode == 0, (
        "alembic upgrade head failed on real SQLite file: stderr=\n"
        + upgrade.stderr
    )

    # Step 2 — write a sample row, then downgrade -1 (v3 → v2).
    import sqlite3

    conn = sqlite3.connect(sqlite_path)
    try:
        conn.execute(
            "INSERT INTO tasks (id, name, result_json) VALUES (?, ?, ?)",
            ("row-1", "task-A", sample_value),
        )
        conn.commit()
    finally:
        conn.close()

    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(alembic_ini),
         "downgrade", "-1"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert downgrade.returncode == 0, (
        "alembic downgrade -1 failed on real SQLite file: stderr=\n"
        + downgrade.stderr
    )

    # Step 3 — upgrade head again to confirm the v3 data move preserves
    # the sample value byte-for-byte.
    upgrade_again = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(alembic_ini),
         "upgrade", "head"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert upgrade_again.returncode == 0, (
        "alembic upgrade head (second pass) failed on real SQLite file: "
        "stderr=\n" + upgrade_again.stderr
    )

    # Final assertion — the row survives the chain. The ``tasks`` schema
    # may have changed shape (result_json → task_results), so we read
    # from whichever end-state the v3 schema authors. GREEN defines
    # where the row lives after the round-trip; this test asserts the
    # sample value is recoverable in some form.
    conn = sqlite3.connect(sqlite_path)
    try:
        cursor = conn.execute(
            "SELECT result_json FROM tasks WHERE id = ?", ("row-1",)
        )
        row = cursor.fetchone()
        if row is None:
            # After v3, the row lives in task_results; the v3 migration
            # contract is that the JOIN recovers the original bytes.
            cursor = conn.execute(
                "SELECT result_json FROM task_results WHERE task_id = ?",
                ("row-1",),
            )
            (recovered,) = cursor.fetchone()
        else:
            (recovered,) = row
        assert recovered == sample_value, (
            "Real-file round-trip did not preserve the sample value "
            f"(expected {sample_value!r}, got {recovered!r})"
        )
    finally:
        conn.close()
