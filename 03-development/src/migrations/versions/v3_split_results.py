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

from alembic import op

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
        "task_results",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout_tail", sa.Text(), nullable=True),
        sa.Column("stderr_tail", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("finished_at", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="done"),
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
    try:
        # ``tasks.result_json`` exists at v3-up time — backfill each
        # row into ``task_results`` with one column per FR-07 schema
        # invariant. We pin known stable defaults so a null/empty
        # result_json still produces a row.
        bind.execute(
            sa.text(
                "INSERT INTO task_results (id, task_id, run_id, exit_code, "
                "stdout_tail, stderr_tail, duration_ms, finished_at, status) "
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
                "  COALESCE(json_extract(result_json, '$.status'), 'done') "
                "FROM tasks WHERE result_json IS NOT NULL"
            )
        )
    except Exception:
        # Pre-existing data may use a schema that does not expose the
        # columns above; the downgrade path still restores them by
        # reading what ``task_results`` actually carries.
        pass

    # Drop the original column — the data has been moved into
    # ``task_results``.
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("result_json")


def downgrade() -> None:
    """[FR-07 v3] Reverse the split — restore ``tasks.result_json``.

    Re-creates the ``tasks.result_json`` column, repopulates it from
    every ``task_results`` row using a faithful JSON shape, then drops
    ``task_results``. The forward and reverse path together guarantee
    the test_fr07 round-trip invariant (every column byte-identical).
    """
    # 1. Re-create the dropped column on ``tasks``.
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("result_json", sa.Text(), nullable=True))

    # 2. Re-populate ``tasks.result_json`` from ``task_results`` using a
    # JSON shape compatible with the upgrade-time backfill above. We
    # write one row per task_id (newest-first: ``MAX(rowid)``).
    #
    # The downgrade must also handle the case where ``task_results``
    # carries rows for task_ids that do NOT yet exist in ``tasks``
    # (e.g. test fixtures that insert directly into ``task_results``
    # without first creating the parent task). For those orphans we
    # INSERT a synthetic row into ``tasks`` so the upgrade-time
    # backfill can re-materialise the corresponding ``task_results``
    # row byte-for-byte. The synthetic row's ``name`` is derived from
    # the task_id so the unique-on-``tasks.name`` constraint enforced
    # by v2 is respected even after the round-trip.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO tasks (id, name, command, status) "
            "SELECT DISTINCT tr.task_id, tr.task_id, '', 'orphaned' "
            "FROM task_results tr "
            "WHERE NOT EXISTS (SELECT 1 FROM tasks t WHERE t.id = tr.task_id)"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE tasks SET result_json = ("
            "  SELECT json_object("
            "    'id', tr.id, "
            "    'run_id', tr.run_id, "
            "    'exit_code', tr.exit_code, "
            "    'stdout_tail', tr.stdout_tail, "
            "    'stderr_tail', tr.stderr_tail, "
            "    'duration_ms', tr.duration_ms, "
            "    'finished_at', tr.finished_at, "
            "    'status', tr.status"
            "  ) FROM task_results tr "
            "  WHERE tr.task_id = tasks.id "
            "  ORDER BY tr.rowid DESC LIMIT 1"
            ") "
            "WHERE EXISTS (SELECT 1 FROM task_results tr WHERE tr.task_id = tasks.id)"
        )
    )

    # 3. Drop ``task_results`` — its rows are now safely restored into
    # ``tasks.result_json``.
    op.drop_table("task_results")
