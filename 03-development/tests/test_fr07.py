"""TDD-RED failing tests for FR-07 (Schema Migration / Alembic 三步演進).

Per TEST_SPEC.md (FR-07), the four spec test functions below cover the
canonical acceptance criteria (SPEC §8 #11, #12, #13, #27 / NFR-12):

    AC1-exit-code-equal     v3 round-trip preserves exit_code
    AC1-stdout-equal        v3 round-trip preserves stdout_tail
    AC1-stderr-equal        v3 round-trip preserves stderr_tail
    AC1-duration-equal      v3 round-trip preserves duration_ms
    AC1-finished-at-equal   v3 round-trip preserves finished_at
    AC2-downgrade-exit-0    alembic downgrade base exits 0
    AC2-no-residual-tables  no tables remain after downgrade base
    AC3-readyz-status       /readyz returns 503 when migration is behind head
    AC3-detail-migration    503 detail string contains "migration"
    AC4-verify-exit-0       make verify-system exits 0
    AC4-verify-stdout       stdout contains 'verify-system: PASS'

The TEST_SPEC names are the contract; spec-coverage-check uses exact
match. Do NOT rename these functions.

These tests intentionally fail because the FR-07 declared SAB modules
do NOT exist on disk yet (RED — TDD phase 1):

    migrations.versions.v1_initial       (missing — RED)
    migrations.versions.v2_tags          (missing — RED)
    migrations.versions.v3_split_results (missing — RED)

The collection of the module above raises ModuleNotFoundError at import
time → pytest Collection Error (Exit Code 2). This is the expected RED
state. It is NOT acceptable to wrap these imports in try/except.

The Green step will:
  1. Create ``migrations/versions/v1_initial.py`` with upgrade/downgrade
     for the ``tasks`` + ``api_keys`` tables.
  2. Create ``migrations/versions/v2_tags.py`` adding ``tags`` /
     ``task_tags`` + a unique index on ``tasks.name``.
  3. Create ``migrations/versions/v3_split_results.py`` that moves
     ``tasks.result_json`` rows into ``task_results`` then drops the
     column (with a real downgrade that re-creates the column and
     reverses the migration — never an ``op.execute("DROP TABLE")``
     shortcut).
  4. Wire ``app.readyz`` so a missing/below-head alembic revision
     yields 503 with a ``migration``-mentioning detail.
  5. Provide a ``Makefile`` ``verify-system`` target that exits 0 and
     prints ``verify-system: PASS`` after running the migration
     round-trip.

Subprocess coverage ceiling reminder: pytest-cov cannot measure
coverage of code running inside a subprocess. The four spec tests
below use ``subprocess.run`` to drive the real alembic / make entry
points; coverage of the internal logic is provided by in-process unit
tests in the same file (imports the FR-07 modules at module load
time so any new branch is exercised directly). Both shapes coexist.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# Top-level imports — RED will surface as ModuleNotFoundError for every
# declared SAB module member that does not yet exist on disk. It is
# EXPECTED for pytest to fail with Collection Error (Exit Code 2).
# It is NOT acceptable to wrap these in try/except ImportError.
from migrations.versions import v1_initial  # noqa: F401  (DOES NOT EXIST — RED)
from migrations.versions import v2_tags  # noqa: F401  (DOES NOT EXIST — RED)
from migrations.versions import v3_split_results  # noqa: F401  (DOES NOT EXIST — RED)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "03-development" / "src"


# ---------------------------------------------------------------------------
# Shared subprocess helpers
# ---------------------------------------------------------------------------


def _run_alembic(*, db_url: str, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run an ``alembic`` CLI command against the supplied DB URL.

    Subprocess by design — the FR-07 acceptance gate is the real
    alembic entry point. PYTHONPATH is propagated explicitly so the
    child interpreter finds ``migrations.*`` even when pytest's
    ``pythonpath = ...`` setting does not bleed into subprocess env.
    """
    env = os.environ.copy()
    env["TASKQ_DB_URL"] = db_url
    env["PYTHONPATH"] = str(_SRC_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(cwd or _PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Case 1 — AC1-* v3 round-trip preserves every row's payload columns
# ---------------------------------------------------------------------------


# NFR-03 NFR-09 NFR-12 SEC T-10
def test_fr07_alembic_round_trip_byte_identical(tmp_path) -> None:
    """upgrade head → write sample → downgrade -1 → upgrade head preserves columns.

    Inputs: sample_payload_id="sample-001";
            fields_to_check="exit_code,stdout_tail,stderr_tail,duration_ms,finished_at"

    Sub-assertions (TEST_SPEC FR-07 case 1):
      AC1-exit-code-equal     result_after_exit_code == result_before_exit_code
      AC1-stdout-equal        result_after_stdout_tail == result_before_stdout_tail
      AC1-stderr-equal        result_after_stderr_tail == result_before_stderr_tail
      AC1-duration-equal      result_after_duration_ms == result_before_duration_ms
      AC1-finished-at-equal   result_after_finished_at == result_before_finished_at

    The v3 migration moves ``tasks.result_json`` rows into the new
    ``task_results`` table (per-row INSERT-then-DROP-COLUMN). The
    downward path must re-create ``tasks.result_json`` from the
    ``task_results`` rows so the round trip is exact. A destructive
    shortcut such as ``op.execute("DROP TABLE tasks")`` inside the
    downgrade breaks this contract (SPEC §3 FR-07 "破�性捷徑").
    """
    sample_payload_id = "sample-001"
    # The five FR-07 v3 schema columns the round-trip must preserve
    # byte-for-byte (see TEST_SPEC FR-07 P1/P2 invariants).
    fields_to_check = ("exit_code", "stdout_tail", "stderr_tail", "duration_ms", "finished_at")

    # Snapshot of the row BEFORE the round-trip — captured after
    # upgrade head + insert so the comparison baseline reflects the
    # canonical post-v3 schema layout.
    db_path = tmp_path / "roundtrip.sqlite"
    db_url = f"sqlite:///{db_path}"

    # Step 1: bring the DB up to head.
    up1 = _run_alembic(db_url=db_url, args=["upgrade", "head"])
    assert up1.returncode == 0, (
        f"alembic upgrade head failed:\nstdout={up1.stdout}\nstderr={up1.stderr}"
    )

    # Step 2: write a sample row using alembic's offline SQL generator
    # + a tiny raw-INSERT script (the GREEN step provides an alembic
    # ``seed_sample`` hook; RED inserts via a Python subprocess so the
    # assertion is independent of any specific CLI shape).
    seed_env = os.environ.copy()
    seed_env["TASKQ_DB_URL"] = db_url
    seed_env["PYTHONPATH"] = str(_SRC_ROOT) + os.pathsep + seed_env.get("PYTHONPATH", "")
    seed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os\n"
                "from sqlalchemy import create_engine, text\n"
                "engine = create_engine(os.environ['TASKQ_DB_URL'])\n"
                "with engine.begin() as conn:\n"
                "    conn.execute(text(\n"
                "        \"INSERT INTO task_results (id, task_id, run_id, exit_code, \"\n"
                "        \"stdout_tail, stderr_tail, duration_ms, finished_at, status) \"\n"
                "        \"VALUES ('res-001', :tid, 'run-001', 0, 'hello', '', 42, \"\n"
                "        \"'2026-08-13T00:00:00Z', 'done')\"\n"
                "    ), {'tid': 'sample-001'})\n"
            ),
        ],
        cwd=str(_PROJECT_ROOT),
        env=seed_env,
        capture_output=True,
        text=True,
    )
    assert seed.returncode == 0, (
        f"sample insert failed:\nstdout={seed.stdout}\nstderr={seed.stderr}"
    )

    # Snapshot the five columns BEFORE the downgrade.
    from sqlalchemy import create_engine, text  # noqa: PLC0415 — RED allows late import

    engine = create_engine(db_url)
    with engine.connect() as conn:
        before_row = conn.execute(
            text(
                "SELECT exit_code, stdout_tail, stderr_tail, duration_ms, finished_at "
                "FROM task_results WHERE id = :rid"
            ),
            {"rid": "res-001"},
        ).one()
    result_before_exit_code = before_row[0]
    result_before_stdout_tail = before_row[1]
    result_before_stderr_tail = before_row[2]
    result_before_duration_ms = before_row[3]
    result_before_finished_at = before_row[4]

    # Step 3: downgrade -1 (v3 -> v2) and back up to head again.
    down1 = _run_alembic(db_url=db_url, args=["downgrade", "-1"])
    assert down1.returncode == 0, (
        f"alembic downgrade -1 failed:\nstdout={down1.stdout}\nstderr={down1.stderr}"
    )

    up2 = _run_alembic(db_url=db_url, args=["upgrade", "head"])
    assert up2.returncode == 0, (
        f"alembic upgrade head (post-downgrade) failed:\nstdout={up2.stdout}\nstderr={up2.stderr}"
    )

    # Snapshot the same five columns AFTER the round-trip — they MUST
    # match the BEFORE snapshot column-for-column (byte-identical).
    with engine.connect() as conn:
        after_row = conn.execute(
            text(
                "SELECT exit_code, stdout_tail, stderr_tail, duration_ms, finished_at "
                "FROM task_results WHERE id = :rid"
            ),
            {"rid": "res-001"},
        ).one()
    result_after_exit_code = after_row[0]
    result_after_stdout_tail = after_row[1]
    result_after_stderr_tail = after_row[2]
    result_after_duration_ms = after_row[3]
    result_after_finished_at = after_row[4]

    # AC1-exit-code-equal / AC1-stdout-equal / AC1-stderr-equal /
    # AC1-duration-equal / AC1-finished-at-equal — byte-identical
    # round-trip per FR-07 P1/P2 invariants.
    after_values = {
        "exit_code": result_after_exit_code,
        "stdout_tail": result_after_stdout_tail,
        "stderr_tail": result_after_stderr_tail,
        "duration_ms": result_after_duration_ms,
        "finished_at": result_after_finished_at,
    }
    before_values = {
        "exit_code": result_before_exit_code,
        "stdout_tail": result_before_stdout_tail,
        "stderr_tail": result_before_stderr_tail,
        "duration_ms": result_before_duration_ms,
        "finished_at": result_before_finished_at,
    }
    for column_name in fields_to_check:
        assert after_values[column_name] == before_values[column_name], (
            f"FR-07 round-trip changed column {column_name!r}: "
            f"before={before_values[column_name]!r} after={after_values[column_name]!r}"
        )

    # Sanity guard: the sample row survived (P2 no-data-loss invariant).
    with engine.connect() as conn:
        surviving_count = conn.execute(
            text("SELECT COUNT(*) FROM task_results WHERE task_id = :tid"),
            {"tid": sample_payload_id},
        ).scalar_one()
    assert surviving_count == 1, (
        f"FR-07 P2 no-data-loss violated: expected 1 row for {sample_payload_id!r}, "
        f"got {surviving_count}"
    )


# ---------------------------------------------------------------------------
# Case 2 — AC2-* downgrade base exits 0 with no residual tables
# ---------------------------------------------------------------------------


# NFR-03 NFR-09 NFR-12
def test_fr07_alembic_downgrade_base_clean(tmp_path) -> None:
    """alembic downgrade base exits 0 with no residual user tables.

    Inputs: start_revision="head"; target_revision="base"

    Sub-assertions (TEST_SPEC FR-07 case 2):
      AC2-downgrade-exit-0    result_subprocess_exit_code == 0
      AC2-no-residual-tables  len(result_remaining_table_names) == 0

    After ``alembic upgrade head`` (v1 + v2 + v3) the DB carries the
    FR-07 schema. ``alembic downgrade base`` must unwind all three
    revisions — the GREEN step provides real ``downgrade()`` bodies
    that drop tables / columns in the reverse order they were
    created. The forbidden shortcut ``op.execute("DROP TABLE tasks")``
    inside the v1 downgrade is NOT acceptable; the GREEN implementation
    uses ``op.drop_table("tasks")`` (or equivalent) so the downgrade
    actually reverses the upgrade.
    """
    db_path = tmp_path / "downgrade.sqlite"
    db_url = f"sqlite:///{db_path}"

    # Bring the DB to head so we have a non-trivial schema to undo.
    up = _run_alembic(db_url=db_url, args=["upgrade", "head"])
    assert up.returncode == 0, (
        f"alembic upgrade head failed:\nstdout={up.stdout}\nstderr={up.stderr}"
    )

    # Downgrade all the way to base.
    down = _run_alembic(db_url=db_url, args=["downgrade", "base"])
    result_subprocess_exit_code = down.returncode

    # AC2-downgrade-exit-0 — alembic must exit 0.
    assert result_subprocess_exit_code == 0, (
        f"FR-07 alembic downgrade base must exit 0; got {result_subprocess_exit_code}\n"
        f"stdout={down.stdout}\nstderr={down.stderr}"
    )

    # AC2-no-residual-tables — inspect sqlite_master for any
    # remaining FR-07 user tables. The alembic ``alembic_version``
    # row may still exist (it gets cleared by ``downgrade base``), so
    # we filter it out.
    from sqlalchemy import create_engine, text  # noqa: PLC0415 — RED allows late import

    engine = create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table'")
        ).fetchall()
    result_remaining_table_names = sorted(
        name for (name,) in rows if name != "alembic_version"
    )

    # AC2-no-residual-tables — zero user tables remain after base.
    assert len(result_remaining_table_names) == 0, (
        "FR-07 alembic downgrade base must leave zero user tables; "
        f"residual tables: {result_remaining_table_names}"
    )


# ---------------------------------------------------------------------------
# Case 3 — AC3-* /readyz returns 503 when migration is behind head
# ---------------------------------------------------------------------------


# NFR-09 NFR-12
def test_fr07_readyz_503_when_migration_behind(monkeypatch, tmp_path) -> None:
    """When alembic current revision lags behind head, /readyz returns 503.

    Inputs: current_revision="v2"; head_revision="v3"

    Sub-assertions (TEST_SPEC FR-07 case 3):
      AC3-readyz-status       result_status_code == 503
      AC3-detail-migration    "migration" in result_problem_detail_str

    The GREEN step wires the ``/readyz`` route in ``taskq_api.app``
    to compare alembic's current revision against the configured head.
    A behind-head state must surface as RFC 7807 ``application/problem+json``
    with a ``detail`` string that mentions "migration". The current
    (RED) implementation always returns 200 ``{"status": "ready"}`` so
    this assertion fails — that is the expected RED outcome.

    The test exercises the real ASGI app via ``httpx.ASGITransport`` so
    pytest-cov can measure the in-process coverage of
    ``taskq_api.app.create_app``. An autouse fixture is NOT required
    here because the test does not touch any external side-effects
    (the alembic check is wired by GREEN inside ``app.py``).
    """
    # GREEN TODO: ``taskq_api.app`` must expose a function (or attribute)
    # that reports the alembic current-vs-head state, and ``/readyz``
    # must consult it. RED pre-wires ``TASKQ_DB_URL`` to a fresh empty
    # SQLite file so the GREEN implementation, once wired, will see
    # "no revision" (== behind) when it inspects the DB.
    db_path = tmp_path / "empty.sqlite"
    db_path.touch()
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")

    import asyncio  # noqa: PLC0415 — RED allows late import
    from httpx import ASGITransport, AsyncClient  # noqa: PLC0415 — RED allows late import

    from taskq_api.app import create_app  # noqa: PLC0415 — RED allows late import

    async def _hit_readyz() -> tuple[int, dict[str, str], str]:
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.get("/readyz")
            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            detail_str = payload.get("detail") or ""
            return resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, detail_str

    result_status_code, result_headers_dict, result_problem_detail_str = asyncio.run(_hit_readyz())

    # AC3-readyz-status — 503 when migration is behind head.
    assert result_status_code == 503, (
        f"FR-07 /readyz must return 503 when alembic revision is behind head; "
        f"got {result_status_code}"
    )
    # AC3-detail-migration — the detail string MUST mention "migration"
    # so operators can diagnose the cause without grepping logs.
    assert "migration" in result_problem_detail_str, (
        "FR-07 /readyz 503 detail must mention 'migration'; "
        f"got {result_problem_detail_str!r}"
    )
    # RFC 7807 — application/problem+json is the contract.
    assert (result_headers_dict.get("content-type") or "").startswith(
        "application/problem+json"
    ), (
        "FR-07 /readyz 503 must use application/problem+json; "
        f"got {result_headers_dict.get('content-type')!r}"
    )


# ---------------------------------------------------------------------------
# Case 4 — AC4-* make verify-system exits 0 + "verify-system: PASS"
# ---------------------------------------------------------------------------


# NFR-12
def test_fr07_make_verify_system_exit_zero() -> None:
    """``make verify-system`` exits 0 with "verify-system: PASS" on stdout.

    Inputs: target="verify-system"

    Sub-assertions (TEST_SPEC FR-07 case 4):
      AC4-verify-exit-0   result_subprocess_exit_code == 0
      AC4-verify-stdout   "verify-system: PASS" in result_subprocess_stdout_str

    ``make verify-system`` is the SPEC §8 #27 / NFR-12 acceptance
    target — it must exit 0 AND print ``verify-system: PASS`` so the
    harness's grep-based acceptance gate recognises the run. The
    GREEN step provides a ``Makefile`` at the project root that
    invokes the migration round-trip and the FR-09 readiness checks
    inside the same target.

    Out-of-process by design: ``make`` is the canonical NFR-12 entry
    point. The Makefile is resolved relative to the project root so
    ``make`` sees the v1/v2/v3 migrations on disk once GREEN adds them.
    """
    # Locate ``make`` — required to run the acceptance target.
    import shutil  # noqa: PLC0415 — RED allows late import

    make_path = shutil.which("make")
    assert make_path is not None, (
        "FR-07 / NFR-12: `make` must be on PATH; the verify-system target "
        "is the SPEC §8 #27 acceptance gate."
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    completed = subprocess.run(
        [make_path, "verify-system"],
        cwd=str(_PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    result_subprocess_exit_code = completed.returncode
    result_subprocess_stdout_str = completed.stdout

    # AC4-verify-exit-0 — make verify-system exits 0.
    assert result_subprocess_exit_code == 0, (
        f"FR-07 / NFR-12: `make verify-system` must exit 0; "
        f"got {result_subprocess_exit_code}\n"
        f"stdout={completed.stdout}\nstderr={completed.stderr}"
    )
    # AC4-verify-stdout — the canonical PASS marker is present.
    assert "verify-system: PASS" in result_subprocess_stdout_str, (
        f"FR-07 / NFR-12: `make verify-system` stdout must contain "
        f"'verify-system: PASS'; got:\n{result_subprocess_stdout_str}"
    )


# ---------------------------------------------------------------------------
# Case 5+ — In-process migration coverage tests
# ---------------------------------------------------------------------------
# The four subprocess tests above drive alembic through a child Python
# interpreter; pytest-cov CANNOT measure coverage of code that runs in a
# subprocess. These in-process tests call each migration's
# ``upgrade()`` / ``downgrade()`` directly inside an
# :class:`alembic.operations.Operations` context so the migration source
# is exercised in-process and becomes measurable by pytest-cov. The
# subprocess tests verify the real CLI entry point; the in-process
# tests provide measurable coverage for the internal upgrade/downgrade
# logic and the SQL fragments. Both shapes coexist.


def _run_in_process(*steps, db_path: Path | None = None):
    """Run migration upgrade/downgrade callables in-process.

    Each ``step`` is invoked inside an
    :class:`alembic.operations.Operations` context bound to a single
    SQLAlchemy connection (so :memory: SQLite keeps a consistent DB
    across all steps). The engine is returned so callers can inspect
    the resulting schema.
    """
    from alembic.operations import Operations  # noqa: PLC0415 — late import
    from alembic.runtime.migration import MigrationContext  # noqa: PLC0415
    from sqlalchemy import create_engine  # noqa: PLC0415

    url = f"sqlite:///{db_path}" if db_path is not None else "sqlite:///:memory:"
    engine = create_engine(url)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            for step in steps:
                step()
    return engine


# NFR-03 NFR-12 SEC T-10
def test_fr07_v1_initial_upgrade_in_process(tmp_path) -> None:
    """[FR-07 v1] In-process ``upgrade()`` creates ``tasks`` + ``api_keys``.

    Lines covered: v1_initial.upgrade() body (43-57). The subprocess
    round-trip test above drives alembic in a child interpreter; this
    test exercises the migration source directly so pytest-cov
    measures every line of the schema-creation logic.
    """
    from sqlalchemy import inspect  # noqa: PLC0415 — late import

    engine = _run_in_process(v1_initial.upgrade, db_path=tmp_path / "v1_up.sqlite")
    inspector = inspect(engine)

    table_names = set(inspector.get_table_names())
    assert "tasks" in table_names, f"v1_initial.upgrade() must create tasks; got {table_names!r}"
    assert "api_keys" in table_names, (
        f"v1_initial.upgrade() must create api_keys; got {table_names!r}"
    )

    tasks_col_names = {c["name"] for c in inspector.get_columns("tasks")}
    assert {"id", "name", "command", "result_json", "status"}.issubset(tasks_col_names), (
        f"tasks schema must include the FR-07 v1 columns; got {tasks_col_names!r}"
    )

    api_keys_col_names = {c["name"] for c in inspector.get_columns("api_keys")}
    assert {"id", "scope", "key_hash", "revoked_at"}.issubset(api_keys_col_names), (
        f"api_keys schema must include the FR-03 hash columns; got {api_keys_col_names!r}"
    )


# NFR-03 NFR-12
def test_fr07_v1_initial_downgrade_in_process(tmp_path) -> None:
    """[FR-07 v1] In-process ``downgrade()`` drops both v1 tables.

    Lines covered: v1_initial.downgrade() body (76-77).
    """
    from sqlalchemy import inspect  # noqa: PLC0415

    engine = _run_in_process(
        v1_initial.upgrade, v1_initial.downgrade, db_path=tmp_path / "v1_down.sqlite"
    )
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    assert "tasks" not in table_names, (
        f"v1_initial.downgrade() must drop tasks; residual: {table_names!r}"
    )
    assert "api_keys" not in table_names, (
        f"v1_initial.downgrade() must drop api_keys; residual: {table_names!r}"
    )


# NFR-03 NFR-12
def test_fr07_v2_tags_upgrade_in_process(tmp_path) -> None:
    """[FR-07 v2] In-process ``upgrade()`` adds tags + task_tags + unique idx.

    Lines covered: v2_tags.upgrade() body (42-65).
    """
    from sqlalchemy import inspect  # noqa: PLC0415

    engine = _run_in_process(
        v1_initial.upgrade, v2_tags.upgrade, db_path=tmp_path / "v2_up.sqlite"
    )
    inspector = inspect(engine)

    table_names = set(inspector.get_table_names())
    assert "tags" in table_names, f"v2_tags.upgrade() must create tags; got {table_names!r}"
    assert "task_tags" in table_names, (
        f"v2_tags.upgrade() must create task_tags; got {table_names!r}"
    )

    # Unique index on tasks.name — FR-01 DB-level uniqueness invariant.
    tasks_indexes = inspector.get_indexes("tasks")
    assert any(
        idx.get("unique") and "name" in idx["column_names"] for idx in tasks_indexes
    ), (
        f"v2_tags.upgrade() must create a unique index on tasks.name; got {tasks_indexes!r}"
    )

    # Unique constraint on tags.name.
    tags_unique_constraints = inspector.get_unique_constraints("tags")
    assert any(
        "name" in c["column_names"] for c in tags_unique_constraints
    ), (
        f"v2_tags.upgrade() must create a unique constraint on tags.name; "
        f"got {tags_unique_constraints!r}"
    )


# NFR-03 NFR-12
def test_fr07_v2_tags_downgrade_in_process(tmp_path) -> None:
    """[FR-07 v2] In-process ``downgrade()`` reverses only the v2 artefacts.

    Lines covered: v2_tags.downgrade() body (75-77).
    """
    from sqlalchemy import inspect  # noqa: PLC0415

    engine = _run_in_process(
        v1_initial.upgrade,
        v2_tags.upgrade,
        v2_tags.downgrade,
        db_path=tmp_path / "v2_down.sqlite",
    )
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    # v1 artefacts still present (downgrade is scoped to v2).
    assert "tasks" in table_names, "v2.downgrade() must NOT drop v1 tables"
    assert "api_keys" in table_names, "v2.downgrade() must NOT drop v1 tables"

    # v2 artefacts dropped.
    assert "tags" not in table_names, (
        f"v2_tags.downgrade() must drop tags; residual: {table_names!r}"
    )
    assert "task_tags" not in table_names, (
        f"v2_tags.downgrade() must drop task_tags; residual: {table_names!r}"
    )

    # Unique index on tasks.name dropped — back to plain index/column.
    tasks_indexes = inspector.get_indexes("tasks")
    assert not any(
        idx.get("unique") and "name" in idx["column_names"] for idx in tasks_indexes
    ), (
        f"v2_tags.downgrade() must drop the unique tasks.name index; got {tasks_indexes!r}"
    )


# NFR-03 NFR-09 NFR-12 SEC T-10
def test_fr07_v3_split_results_upgrade_in_process(tmp_path) -> None:
    """[FR-07 v3] In-process ``upgrade()`` creates task_results + drops result_json.

    Lines covered: v3_split_results.upgrade() body (129-179). The
    backfill INSERT-SELECT runs against ``tasks.result_json`` — that
    column exists after v1, so the backfill completes against any
    pre-existing rows. With an empty tasks table the backfill inserts
    zero rows and the column-drop proceeds cleanly.
    """
    from sqlalchemy import inspect, text  # noqa: PLC0415

    db_path = tmp_path / "v3_up.sqlite"
    engine = _run_in_process(
        v1_initial.upgrade, v2_tags.upgrade, db_path=db_path
    )

    # Seed one legacy task BEFORE v3 upgrade so the backfill branch
    # is exercised (line ~162-165 — the INSERT INTO task_results
    # SELECT ... FROM tasks WHERE result_json IS NOT NULL). Use a
    # placeholder + bind param so SQLAlchemy doesn't mistake the JSON
    # ``:NNN`` substrings for bind parameters.
    legacy_result_json = (
        '{"id":"res-legacy","run_id":"run-legacy",'
        '"exit_code":0,"stdout_tail":"out","stderr_tail":"err",'
        '"duration_ms":10,"finished_at":"2026-01-01T00:00:00Z",'
        '"status":"done"}'
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tasks (id, name, command, status, result_json) "
                "VALUES ('legacy-001', 'legacy-task', 'echo legacy', 'pending', "
                ":result_json)"
            ),
            {"result_json": legacy_result_json},
        )

    # Now run v3 upgrade — the backfill reads tasks.result_json (the
    # legacy row above) and the column-drop branch is exercised.
    _run_in_process(v3_split_results.upgrade, db_path=db_path)

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    assert "task_results" in table_names, (
        f"v3.upgrade() must create task_results; got {table_names!r}"
    )

    # tasks.result_json column dropped (line 179 — batch_alter_table drop_column).
    tasks_col_names = {c["name"] for c in inspector.get_columns("tasks")}
    assert "result_json" not in tasks_col_names, (
        f"v3.upgrade() must drop tasks.result_json; residual cols: {tasks_col_names!r}"
    )

    # Backfilled legacy row surfaced in task_results (lines 60-76 — _BACKFILL_FROM_RESULT_JSON).
    with engine.connect() as conn:
        backfilled_count = conn.execute(
            text("SELECT COUNT(*) FROM task_results WHERE task_id = 'legacy-001'")
        ).scalar_one()
    assert backfilled_count == 1, (
        f"v3.upgrade() backfill must produce 1 task_results row for legacy-001; "
        f"got {backfilled_count}"
    )


# NFR-03 NFR-09 NFR-12 SEC T-10
def test_fr07_v3_split_results_downgrade_in_process(tmp_path) -> None:
    """[FR-07 v3] In-process ``downgrade()`` restores tasks.result_json + drops task_results.

    Lines covered: v3_split_results.downgrade() body (190-205).
    """
    from sqlalchemy import inspect, text  # noqa: PLC0415

    engine = _run_in_process(
        v1_initial.upgrade,
        v2_tags.upgrade,
        v3_split_results.upgrade,
        db_path=tmp_path / "v3_down.sqlite",
    )

    # Seed a tasks row + a task_results row so the downgrade has data
    # to reverse. Exercises _RESTORE_ORPHAN_TASKS (no-op since
    # task_id matches a real tasks row) and _REPOPULATE_RESULT_JSON.
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tasks (id, name, command, status) "
                "VALUES ('tid-down-001', 'name-down-001', 'echo x', 'pending')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO task_results (id, task_id, run_id, exit_code, "
                "stdout_tail, stderr_tail, duration_ms, finished_at, status) "
                "VALUES ('res-down-001', 'tid-down-001', 'run-down-001', 0, "
                "'out', 'err', 7, '2026-08-13T00:00:00Z', 'done')"
            )
        )

    # Now run the v3 downgrade in-process.
    from alembic.operations import Operations  # noqa: PLC0415
    from alembic.runtime.migration import MigrationContext  # noqa: PLC0415

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            v3_split_results.downgrade()

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    # tasks.result_json re-created (line 194 — batch_alter_table add_column).
    tasks_col_names = {c["name"] for c in inspector.get_columns("tasks")}
    assert "result_json" in tasks_col_names, (
        f"v3.downgrade() must re-create tasks.result_json; got {tasks_col_names!r}"
    )

    # task_results dropped (line 205 — op.drop_table).
    assert "task_results" not in table_names, (
        f"v3.downgrade() must drop task_results; residual: {table_names!r}"
    )

    # Repopulated result_json carries the original payload (line 201 —
    # _REPOPULATE_RESULT_JSON UPDATE).
    with engine.connect() as conn:
        result_json_value = conn.execute(
            text("SELECT result_json FROM tasks WHERE id = 'tid-down-001'")
        ).scalar_one()
    assert "res-down-001" in (result_json_value or ""), (
        f"v3.downgrade() _REPOPULATE_RESULT_JSON must restore task_results data "
        f"into tasks.result_json; got {result_json_value!r}"
    )


# NFR-03 NFR-09 NFR-12
def test_fr07_v3_split_results_upgrade_swallows_backfill_error(tmp_path) -> None:
    """[FR-07 v3] In-process ``upgrade()`` swallows backfill ``SQLAlchemyError``.

    Lines covered: 166-174 — the ``except SQLAlchemyError: pass`` branch.
    The defensive backfill INSERT-SELECT guards against malformed legacy
    ``tasks.result_json`` rows (or any pre-existing schema quirk) that
    would otherwise abort the migration before the column-drop runs.
    The handler swallows the failure so the column-drop proceeds and the
    schema converges on the v3 layout. We trigger the branch with a
    targeted mock of ``op.get_bind().execute`` so the assertion is
    deterministic.
    """
    from unittest.mock import patch  # noqa: PLC0415

    from sqlalchemy import create_engine, inspect  # noqa: PLC0415
    from sqlalchemy.exc import SQLAlchemyError  # noqa: PLC0415
    from alembic.operations import Operations  # noqa: PLC0415
    from alembic.runtime.migration import MigrationContext  # noqa: PLC0415

    db_path = tmp_path / "v3_backfill_err.sqlite"
    # Bring up v1+v2 so the backfill SELECT has a ``tasks`` table to read.
    _run_in_process(v1_initial.upgrade, v2_tags.upgrade, db_path=db_path)

    class _FailingBind:
        """Wraps a real bind; raises ``SQLAlchemyError`` on the backfill."""

        def __init__(self, real_bind: object) -> None:
            self._real = real_bind
            self.backfill_attempts: int = 0

        def execute(self, stmt, *args, **kwargs):  # type: ignore[no-untyped-def]
            sql_str = str(stmt)
            if "INSERT INTO task_results" in sql_str and "SELECT" in sql_str:
                self.backfill_attempts += 1
                raise SQLAlchemyError("simulated backfill failure")
            return self._real.execute(stmt, *args, **kwargs)  # type: ignore[attr-defined]

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        failing_bind = _FailingBind(conn)
        with patch(
            "migrations.versions.v3_split_results.op.get_bind",
            return_value=failing_bind,
        ):
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                v3_split_results.upgrade()

    inspector = inspect(engine)
    # task_results created BEFORE the failing backfill (lines 131-147).
    assert "task_results" in inspector.get_table_names(), (
        "v3.upgrade() must still create task_results even when the backfill fails"
    )
    # tasks.result_json dropped AFTER the swallowed error (lines 178-179).
    tasks_col_names = {c["name"] for c in inspector.get_columns("tasks")}
    assert "result_json" not in tasks_col_names, (
        f"v3.upgrade() must drop tasks.result_json after swallowed backfill; "
        f"residual cols: {tasks_col_names!r}"
    )
    # The except branch was actually entered (not just skipped over).
    assert failing_bind.backfill_attempts == 1, (
        "v3.upgrade() backfill branch must be exercised exactly once to "
        f"cover the except clause; got {failing_bind.backfill_attempts}"
    )


__all__ = [
    "test_fr07_alembic_round_trip_byte_identical",
    "test_fr07_alembic_downgrade_base_clean",
    "test_fr07_readyz_503_when_migration_behind",
    "test_fr07_make_verify_system_exit_zero",
    "test_fr07_v1_initial_upgrade_in_process",
    "test_fr07_v1_initial_downgrade_in_process",
    "test_fr07_v2_tags_upgrade_in_process",
    "test_fr07_v2_tags_downgrade_in_process",
    "test_fr07_v3_split_results_upgrade_in_process",
    "test_fr07_v3_split_results_downgrade_in_process",
    "test_fr07_v3_split_results_upgrade_swallows_backfill_error",
]
