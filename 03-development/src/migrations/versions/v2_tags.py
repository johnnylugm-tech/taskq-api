# pragma: no error-handling
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

# Schema identifiers — pinned at module scope so upgrade() and
# downgrade() share the same names by reference. The unique-index
# name is the explicit anchor for the FR-01 DB-level uniqueness
# invariant, so it MUST be referenced (not duplicated) in both paths.
_TASKS_TABLE: str = "tasks"
_TAGS_TABLE: str = "tags"
_TASK_TAGS_TABLE: str = "task_tags"
_ID_COLUMN_LENGTH: int = 36
_TAG_NAME_COLUMN_LENGTH: int = 64
_TAGGED_AT_COLUMN_LENGTH: int = 64
_UNIQUE_TASKS_NAME_INDEX: str = "uq_tasks_name"
_UNIQUE_TAGS_NAME_CONSTRAINT: str = "uq_tags_name"

revision: str = "v2_tags"
down_revision: str | None = "v1_initial"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """[FR-07 v2] Add tags tables and the unique ``tasks.name`` index."""
    op.create_table(
        _TAGS_TABLE,
        sa.Column("id", sa.String(length=_ID_COLUMN_LENGTH), primary_key=True),
        sa.Column("name", sa.String(length=_TAG_NAME_COLUMN_LENGTH), nullable=False),
        sa.UniqueConstraint("name", name=_UNIQUE_TAGS_NAME_CONSTRAINT),
    )

    op.create_table(
        _TASK_TAGS_TABLE,
        sa.Column("task_id", sa.String(length=_ID_COLUMN_LENGTH), nullable=False),
        sa.Column("tag_id", sa.String(length=_ID_COLUMN_LENGTH), nullable=False),
        sa.Column(
            "tagged_at",
            sa.String(length=_TAGGED_AT_COLUMN_LENGTH),
            nullable=False,
            server_default="",
        ),
        sa.PrimaryKeyConstraint("task_id", "tag_id"),
    )

    # Unique index on tasks.name — FR-01 uniqueness invariant at DB
    # level so the service layer race condition cannot create a
    # duplicate. Named explicitly so the downgrade can target it.
    op.create_index(_UNIQUE_TASKS_NAME_INDEX, _TASKS_TABLE, ["name"], unique=True)


def downgrade() -> None:
    """[FR-07 v2] Reverse ONLY the v2 artefacts — preserve v1 data.

    Drops the unique index on ``tasks.name``, then the ``task_tags``
    join table, then the ``tags`` master table. v1's ``tasks`` and
    ``api_keys`` rows are untouched.
    """
    op.drop_index(_UNIQUE_TASKS_NAME_INDEX, table_name=_TASKS_TABLE)
    op.drop_table(_TASK_TAGS_TABLE)
    op.drop_table(_TAGS_TABLE)
