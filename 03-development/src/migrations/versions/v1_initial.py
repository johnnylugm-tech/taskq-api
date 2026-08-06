"""v1_initial — create base tables (tasks, api_keys, rate_buckets). [FR-07]

Citations: SPEC.md §3 FR-07 (AC-7.1, AC-7.3); SAD.md §2.3.10.

This is the base revision (``down_revision is None``); alembic resolves
``base`` -> ``v1_initial`` and ``head`` -> ``v3_split_results``. The
``tasks`` table carries the FR-01 columns; ``api_keys`` and
``rate_buckets`` carry FR-03 / FR-05 respectively. ``result_json`` lives
on ``tasks`` here and is moved to ``task_results`` by v3.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "v1_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the v1 base tables. [FR-07]

    Citations: SPEC.md §3 FR-07.
    """
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("command", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("plaintext", sa.String(length=128), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False, server_default="read"),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "rate_buckets",
        sa.Column("key_id", sa.String(length=36), primary_key=True),
        sa.Column("tokens", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_refill_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop every v1 base table. [FR-07]

    Citations: SPEC.md §3 FR-07 (AC-7.1, AC-7.3).
    """
    op.drop_table("rate_buckets")
    op.drop_table("api_keys")
    op.drop_table("tasks")
