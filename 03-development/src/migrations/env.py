"""Alembic environment for FR-07.

[FR-07]
Citations: SPEC.md §3 FR-07 (AC-7.1..AC-7.5); SAD.md §2.3.10, §3.6.

``migrations.env`` wires alembic's transaction boundary so a whole
upgrade or downgrade either commits atomically or rolls back — the
NFR-03 atomicity guarantee (TRACEABILITY §5.1 line 414:
``migrations.env`` carries FR-07 + NFR-03). Offline (``--sql``) mode
emits the DDL to the config's output buffer without touching a real
database, which is what AC-7.4 drives.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool


config = context.config

# AC-7.5 — ``TASKQ_DATABASE_URL`` is the same env var
# ``taskq_api.repository.session.engine_from_env`` reads (SPEC §5.1),
# so the alembic CLI and the FastAPI app agree on the database URL.
_TASKQ_DATABASE_URL = "TASKQ_DATABASE_URL"
_TASKQ_DATABASE_URL_DEFAULT = "sqlite:///./taskq.db"


def _resolve_database_url() -> str:
    """Return the database URL honouring ``TASKQ_DATABASE_URL``."""
    return os.environ.get(_TASKQ_DATABASE_URL, _TASKQ_DATABASE_URL_DEFAULT)  # pragma: no cover


# Only inject the env-derived URL when the caller hasn't already set one
# on the Config (the in-process acceptance tests pin a throwaway SQLite
# file via ``config.set_main_option`` and must NOT be shadowed by the
# process-level env var).
_existing_url = config.get_main_option("sqlalchemy.url")
if not _existing_url:  # pragma: no cover
    config.set_main_option("sqlalchemy.url", _resolve_database_url())  # pragma: no cover

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in offline mode (emit SQL only). [FR-07]

    Citations: SPEC.md §3 FR-07 (AC-7.4).
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection. [FR-07]

    Citations: SPEC.md §3 FR-07 (AC-7.1, AC-7.5); NFR-03.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        # NFR-03 — the ``begin_transaction`` block makes the whole
        # upgrade/downgrade a single transaction; alembic rolls back on
        # any exception so a partially applied migration is impossible.
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
