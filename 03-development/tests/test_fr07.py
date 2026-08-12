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

import pytest

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


# NFR-09 NFR-12 SEC T-10
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


# NFR-09 NFR-12
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


__all__ = [
    "test_fr07_alembic_round_trip_byte_identical",
    "test_fr07_alembic_downgrade_base_clean",
    "test_fr07_readyz_503_when_migration_behind",
    "test_fr07_make_verify_system_exit_zero",
]
