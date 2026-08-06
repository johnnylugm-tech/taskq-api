"""v3_split_results — split ``tasks.result_json`` into ``task_results``. [FR-07]

Citations: SPEC.md §3 FR-07 (AC-7.1, AC-7.2, AC-7.3, AC-7.4, AC-7.5);
            SAD.md §2.3.10, §3.6.

This is the data-moving revision. ``tasks.result_json`` is moved into a
dedicated ``task_results(task_id, result_json)`` table; the downgrade
reverses the move (copy BACK, then drop ``task_results``) so no row's
payload is lost across an upgrade/downgrade/upgrade round-trip.

Both directions of the data move are written as bulk SQL via
``op.execute(sa.text("INSERT ... SELECT ..."))`` — offline ``--sql``
mode (AC-7.4) has no live connection to iterate over, so a Python row
loop over ``op.get_bind()`` would make AC-7.4 unsatisfiable. Writing
the move as a single SQL statement is what makes the offline DDL emit
the same data copy.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "v3_split_results"
down_revision = "v2_tags"
branch_labels = None
depends_on = None

# AC-7.4 — both data-move statements are written as bulk SQL so offline
# ``--sql`` mode (no live connection to iterate over) emits the same copy.
_COPY_RESULTS_TO_TASK_RESULTS_SQL = (
    "INSERT INTO task_results (task_id, result_json) "
    "SELECT id, result_json FROM tasks WHERE result_json IS NOT NULL"
)
_COPY_RESULTS_FROM_TASK_RESULTS_SQL = (
    "UPDATE tasks "
    "SET result_json = ("
    "SELECT tr.result_json FROM task_results tr "
    "WHERE tr.task_id = tasks.id"
    ") "
    "WHERE id IN (SELECT task_id FROM task_results)"
)


def upgrade() -> None:
    """Split ``tasks.result_json`` into ``task_results``. [FR-07]

    Citations: SPEC.md §3 FR-07 (AC-7.1, AC-7.2, AC-7.4, AC-7.5).
    """
    op.create_table(
        "task_results",
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("result_json", sa.Text(), nullable=True),
    )
    op.execute(sa.text(_COPY_RESULTS_TO_TASK_RESULTS_SQL))
    op.drop_column("tasks", "result_json")


def downgrade() -> None:
    """Reverse the split: re-add ``tasks.result_json`` and copy BACK. [FR-07]

    Citations: SPEC.md §3 FR-07 (AC-7.2, AC-7.3).

    The reverse copy MUST run BEFORE ``task_results`` is dropped — that
    is the whole point of AC-7.2 (no data loss across
    upgrade -> downgrade -> upgrade). A ``DROP TABLE`` shortcut would
    destroy the payload and fail the round-trip test.
    """
    op.add_column("tasks", sa.Column("result_json", sa.Text(), nullable=True))
    op.execute(sa.text(_COPY_RESULTS_FROM_TASK_RESULTS_SQL))
    op.drop_table("task_results")
