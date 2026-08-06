"""v2_tags — add tags / task_tags many-to-many + unique index on tasks.name. [FR-07]

Citations: SPEC.md §3 FR-07 (AC-7.1, AC-7.3); SAD.md §2.3.10.

Downgrade drops the new tables and the index without touching v1 data —
``tasks``, ``api_keys``, and ``rate_buckets`` survive a downgrade -1.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "v2_tags"
down_revision = "v1_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add tag tables and the unique index on ``tasks.name``. [FR-07]

    Citations: SPEC.md §3 FR-07.
    """
    op.create_table(
        "tags",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
    )
    op.create_table(
        "task_tags",
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("tag_id", sa.String(length=36), sa.ForeignKey("tags.id"), primary_key=True),
    )
    op.create_index(
        "ux_tasks_name",
        "tasks",
        ["name"],
        unique=True,
    )


def downgrade() -> None:
    """Drop tag tables and the unique index; v1 data untouched. [FR-07]

    Citations: SPEC.md §3 FR-07 (AC-7.1, AC-7.3).
    """
    op.drop_index("ux_tasks_name", table_name="tasks")
    op.drop_table("task_tags")
    op.drop_table("tags")
