"""[FR-07] v2_tags — adds ``tags``/``task_tags`` plus unique idx on ``tasks.name``.

Citations:
- SPEC.md §3 FR-07 v2 — add the ``tags`` table, the many-to-many
  ``task_tags`` join table, and a UNIQUE INDEX on ``tasks.name`` so
  duplicate task names are rejected at the DB layer (FR-01 uniqueness).
- SPEC.md §3 FR-07 v2 — the v2 downgrade drops ONLY the new artefacts
  (the ``task_tags`` + ``tags`` tables and the unique index) without
  touching v1 data, so the round-trip preserves existing rows.

Revision ID: v2_tags
Revises: v1_initial
Create Date: 2026-08-13 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "v2_tags"
down_revision: str | None = "v1_initial"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """[FR-07 v2] Add tags tables and the unique ``tasks.name`` index."""
    op.create_table(
        "tags",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("name", name="uq_tags_name"),
    )

    op.create_table(
        "task_tags",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("tag_id", sa.String(length=36), nullable=False),
        sa.Column(
            "tagged_at",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
        sa.PrimaryKeyConstraint("task_id", "tag_id"),
    )

    # Unique index on tasks.name — FR-01 uniqueness invariant at DB
    # level so the service layer race condition cannot create a
    # duplicate. Named explicitly so the downgrade can target it.
    op.create_index("uq_tasks_name", "tasks", ["name"], unique=True)


def downgrade() -> None:
    """[FR-07 v2] Reverse ONLY the v2 artefacts — preserve v1 data.

    Drops the unique index on ``tasks.name``, then the ``task_tags``
    join table, then the ``tags`` master table. v1's ``tasks`` and
    ``api_keys`` rows are untouched.
    """
    op.drop_index("uq_tasks_name", table_name="tasks")
    op.drop_table("task_tags")
    op.drop_table("tags")
