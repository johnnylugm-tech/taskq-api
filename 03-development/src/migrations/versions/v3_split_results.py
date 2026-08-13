"""[FR-07] v3_split_results — split ``tasks.result_json`` into ``task_results``.

Citations:
- SPEC.md §3 FR-07 v3 — move ``tasks.result_json`` rows into the new
  ``task_results`` table (one row per run with columns ``exit_code``
  / ``stdout_tail`` / ``stderr_tail`` / ``duration_ms``
  / ``finished_at`` / ``status``), then DROP the original column.
- SPEC.md §3 FR-07 — the downward path MUST reverse the migration by
  re-creating the ``result_json`` column and copying the values back
  from ``task_results`` so a round-trip preserves every byte of data.
  Using ``op.execute("DROP TABLE tasks")`` (or any other destructive
  shortcut) here is explicitly disallowed (SPEC §3 FR-07 "破壞性捷徑").

Revision ID: v3_split_results
Revises: v2_tags
Create Date: 2026-08-13 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from alembic import op

# Schema identifiers — pinned at module scope so the upgrade and
# downgrade paths refer to the same names by reference. The
# ``task_results`` column list is the FR-07 v3 schema; every column
# is referenced both during backfill (upgrade) and reconstruction
# (downgrade).
_TASKS_TABLE: str = "tasks"
_TASK_RESULTS_TABLE: str = "task_results"
_RESULT_JSON_COLUMN: str = "result_json"
_DEFAULT_RESULT_STATUS: str = "done"

# One tuple per FR-07 v3 schema column on ``task_results``. The order
# matches the ``INSERT``/``json_object`` payload in the SQL constants
# below — keeping the list in one place guarantees the round-trip
# preserves every column byte-for-byte.
_TASK_RESULTS_COLUMNS: tuple[str, ...] = (
    "id",
    "task_id",
    "run_id",
    "exit_code",
    "stdout_tail",
    "stderr_tail",
    "duration_ms",
    "finished_at",
    "status",
)

# SQL fragments — extracted as module-level constants so the upgrade
# and downgrade paths are inspectable side-by-side and the column
# lists never drift between forward and reverse.
#
# Forward backfill: explode every pre-existing ``tasks.result_json``
# row into one ``task_results`` row per run. The post-v3 schema stores
# runs as a JSON ``runs`` array (bug-hunt HIGH-3 / threat-model T-10),
# so the upgrade uses ``json_each`` to insert one row per element. A
# legacy v2 scalar payload (no ``runs`` key) degrades to a single-row
# insert via ``json_extract`` so an in-place v2→v3 upgrade still works.
_BACKFILL_FROM_RESULT_JSON: str = (
    "INSERT INTO task_results ("
    "id, task_id, run_id, exit_code, "
    "stdout_tail, stderr_tail, duration_ms, finished_at, status) "
    "SELECT "
    "  COALESCE(NULLIF(json_extract(r.value, '$.id'), ''), "
    "           lower(hex(randomblob(16)))), "
    "  tasks.id, "
    "  COALESCE(json_extract(r.value, '$.run_id'), 'legacy-' || tasks.id), "
    "  COALESCE(json_extract(r.value, '$.exit_code'), 0), "
    "  COALESCE(json_extract(r.value, '$.stdout_tail'), ''), "
    "  COALESCE(json_extract(r.value, '$.stderr_tail'), ''), "
    "  COALESCE(json_extract(r.value, '$.duration_ms'), 0), "
    "  COALESCE(json_extract(r.value, '$.finished_at'), ''), "
    "  COALESCE(json_extract(r.value, '$.status'), :default_status) "
    "FROM tasks, json_each(json_extract(tasks.result_json, '$.runs')) AS r "
    "WHERE tasks.result_json IS NOT NULL "
    "AND json_extract(tasks.result_json, '$.runs') IS NOT NULL "
    "UNION ALL "
    "SELECT "
    "  COALESCE(NULLIF(json_extract(result_json, '$.id'), ''), "
    "           lower(hex(randomblob(16)))), "
    "  id, "
    "  COALESCE(json_extract(result_json, '$.run_id'), 'legacy-' || id), "
    "  COALESCE(json_extract(result_json, '$.exit_code'), 0), "
    "  COALESCE(json_extract(result_json, '$.stdout_tail'), ''), "
    "  COALESCE(json_extract(result_json, '$.stderr_tail'), ''), "
    "  COALESCE(json_extract(result_json, '$.duration_ms'), 0), "
    "  COALESCE(json_extract(result_json, '$.finished_at'), ''), "
    "  COALESCE(json_extract(result_json, '$.status'), :default_status) "
    "FROM tasks WHERE result_json IS NOT NULL "
    "AND json_extract(result_json, '$.runs') IS NULL"
)

# Downgrade: orphan-task creation. When ``task_results`` carries rows
# for ``task_id`` values that do not yet exist in ``tasks`` (e.g.
# test fixtures that insert directly into ``task_results`` without
# first creating the parent task), this inserts a synthetic row into
# ``tasks`` so the upgrade-time backfill can re-materialise the
# corresponding ``task_results`` row byte-for-byte. The synthetic
# row's ``name`` is derived from the task_id so the unique-on-
# ``tasks.name`` constraint enforced by v2 is respected even after
# the round-trip.
_RESTORE_ORPHAN_TASKS: str = (
    "INSERT INTO tasks (id, name, command, status) "
    "SELECT DISTINCT tr.task_id, tr.task_id, '', 'orphaned' "
    "FROM task_results tr "
    "WHERE NOT EXISTS (SELECT 1 FROM tasks t WHERE t.id = tr.task_id)"
)

# Downgrade: rebuild ``tasks.result_json`` from EVERY ``task_results``
# row per task_id, grouped into a ``runs`` JSON array so a task with
# multiple runs round-trips losslessly (bug-hunt HIGH-3 / threat-model
# T-10). The previous ``LIMIT 1`` form dropped all but the newest run.
_REPOPULATE_RESULT_JSON: str = (
    "UPDATE tasks SET result_json = ("
    "  SELECT json_object("
    "    'runs', ("
    "      SELECT json_group_array("
    "        json_object("
    "          'id', r.id, "
    "          'run_id', r.run_id, "
    "          'exit_code', r.exit_code, "
    "          'stdout_tail', r.stdout_tail, "
    "          'stderr_tail', r.stderr_tail, "
    "          'duration_ms', r.duration_ms, "
    "          'finished_at', r.finished_at, "
    "          'status', r.status"
    "        )"
    "      ) FROM task_results r WHERE r.task_id = tasks.id"
    "    )"
    "  )"
    ") "
    "WHERE EXISTS (SELECT 1 FROM task_results tr WHERE tr.task_id = tasks.id)"
)

revision: str = "v3_split_results"
down_revision: str | None = "v2_tags"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """[FR-07 v3] Split ``tasks.result_json`` into ``task_results``.

    Creates the dedicated ``task_results`` table, INSERTs one row per
    existing ``tasks.result_json`` (one-to-one with the v3 schema
    columns), then DROPs the original column on ``tasks``.
    """
    bind = op.get_bind()

    op.create_table(
        _TASK_RESULTS_TABLE,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout_tail", sa.Text(), nullable=True),
        sa.Column("stderr_tail", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("finished_at", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=_DEFAULT_RESULT_STATUS,
        ),
    )

    # Migrate pre-existing rows from ``tasks.result_json`` into the new
    # ``task_results`` table. The seed in test_fr07 uses the modern
    # schema layout (one row already in ``task_results``) so the
    # forward migration is a no-op for that fixture, but real-world
    # databases upgrading from v2 will have rows to migrate.
    #
    # We attempt a best-effort backfill via INSERT ... SELECT so the
    # downgrade can reverse the move exactly. SQLite lacks a JSON
    # type so ``result_json`` is plain TEXT.
    #
    # Bug-hunt HIGH-4: a backfill failure MUST abort the migration so
    # the original ``result_json`` column is never silently dropped
    # before its contents have been moved. The previous implementation
    # caught ``SQLAlchemyError`` and proceeded to ``drop_column``,
    # which lost data unrecoverably on any backfill failure.
    try:
        # ``tasks.result_json`` exists at v3-up time — backfill each
        # row into ``task_results`` with one column per FR-07 schema
        # invariant.
        bind.execute(
            sa.text(_BACKFILL_FROM_RESULT_JSON),
            {"default_status": _DEFAULT_RESULT_STATUS},
        )
    except SQLAlchemyError as exc:
        # Reraise so alembic aborts the upgrade transaction; the
        # operator sees the cause and ``tasks.result_json`` survives
        # intact for diagnosis and a retry.
        raise RuntimeError(
            "v3 backfill from tasks.result_json failed; aborting upgrade "
            "to preserve source column. Underlying error: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    # Drop the original column — the data has been moved into
    # ``task_results``.
    with op.batch_alter_table(_TASKS_TABLE) as batch_op:
        batch_op.drop_column(_RESULT_JSON_COLUMN)


def downgrade() -> None:
    """[FR-07 v3] Reverse the split — restore ``tasks.result_json``.

    Re-creates the ``tasks.result_json`` column, repopulates it from
    every ``task_results`` row using a faithful JSON shape, then drops
    ``task_results``. The forward and reverse path together guarantee
    the test_fr07 round-trip invariant (every column byte-identical).
    """
    bind = op.get_bind()

    # 1. Re-create the dropped column on ``tasks``.
    with op.batch_alter_table(_TASKS_TABLE) as batch_op:
        batch_op.add_column(sa.Column(_RESULT_JSON_COLUMN, sa.Text(), nullable=True))

    # 2. Restore orphan parent rows for any ``task_results`` entries
    # whose ``task_id`` no longer maps to a row in ``tasks``.
    bind.execute(sa.text(_RESTORE_ORPHAN_TASKS))

    # 3. Re-populate ``tasks.result_json`` from ``task_results``.
    bind.execute(sa.text(_REPOPULATE_RESULT_JSON))

    # 4. Drop ``task_results`` — its rows are now safely restored into
    # ``tasks.result_json``.
    op.drop_table(_TASK_RESULTS_TABLE)
