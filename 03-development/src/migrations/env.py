"""[FR-07] Alembic environment — boots the migration runner.

Citations:
- SPEC.md §3 FR-07 — three Alembic revisions, each with a working
  ``downgrade()`` (no destructive shortcut).
- SAD.md §3.3 — alembic's online/offline runners live here so the
  CLI invocation ``python3 -m alembic upgrade head`` finds the v1 /
  v2 / v3 revisions registered under
  ``03-development/src/migrations/versions/``.

The DB URL is read from the ``TASKQ_DB_URL`` environment variable so
the same alembic invocation can target any database (test, staging,
production) without modifying ``alembic.ini``. When the environment
variable is absent, the runner falls back to ``sqlalchemy.url`` from
the ``.ini`` so the offline SQL generator in the test harness keeps
working.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from alembic.config import Config as AlembicConfig

# ``alembic.ini`` lives at the project root. ``env.py`` is loaded
# relative to its ``script_location`` so the file path resolves
# across all alembic invocations. The repo always ships
# ``alembic.ini`` so the path resolves unconditionally.
_CONFIG_INI_NAME = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "alembic.ini")
)
config = AlembicConfig(_CONFIG_INI_NAME)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The package's ``__init__.py`` exports nothing; alembic discovers
# the v1_initial / v2_tags / v3_split_results modules via the
# ``versions/`` sub-package import (each module's module-level
# ``revision`` / ``down_revision`` globals drive the chain).
target_metadata = None


def _resolve_url() -> str:
    """[FR-07] Pick the DB URL — env var first, then ``alembic.ini``."""
    env_url = os.environ.get("TASKQ_DB_URL", "")
    if env_url:
        return env_url
    ini_url = config.get_main_option("sqlalchemy.url") or ""
    return ini_url


def run_migrations_offline() -> None:
    """[FR-07] Emit SQL statements to stdout (no DB connection)."""
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """[FR-07] Execute migrations against the configured DB."""
    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
