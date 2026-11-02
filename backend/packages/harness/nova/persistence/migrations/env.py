"""Alembic environment for DeerFlow application tables.

ONLY manages DeerFlow's tables (runs, threads_meta, cron_jobs, users).
LangGraph's checkpointer tables are managed by LangGraph itself -- they
have their own schema lifecycle and must not be touched by Alembic.
"""

from __future__ import annotations

import asyncio
import logging
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.base import Base

# Import all models so metadata is populated.
try:
    import deerflow.persistence.models as models  # register ORM models with Base.metadata

    _ = models
except ImportError:
    # Models not available — migration will work with existing metadata only.
    logging.getLogger(__name__).warning("Could not import deerflow.persistence.models; Alembic may not detect all tables")

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False: the default silently disables every
    # logger created before this point. Harmless for the CLI, but when the
    # migrations run in-process (boot, tests) it muted the application's
    # loggers — caplog-based tests after the migration test all went dark.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


VERSION_TABLE = "alembic_version"
VERSION_NUM_WIDTH = 128


def _ensure_wide_version_table(connection) -> None:
    """Own ``alembic_version`` so revision ids longer than 32 chars fit.

    Alembic creates the table with ``version_num VARCHAR(32)``. Several
    revision ids in this repo are 33–34 characters; SQLite ignores the
    length, Postgres enforces it, and the very first ``upgrade head`` on the
    live database died on the stamp (2026-09-19). Creating the table
    ourselves (and widening it if an older one exists) runs before Alembic
    looks for it, so it reuses ours.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    if not inspector.has_table(VERSION_TABLE):
        connection.execute(text(f"CREATE TABLE {VERSION_TABLE} (version_num VARCHAR({VERSION_NUM_WIDTH}) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"))
        return
    if connection.dialect.name == "postgresql":
        connection.execute(text(f"ALTER TABLE {VERSION_TABLE} ALTER COLUMN version_num TYPE VARCHAR({VERSION_NUM_WIDTH})"))


def do_run_migrations(connection):
    _ensure_wide_version_table(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # Required for SQLite ALTER TABLE support
    )
    with context.begin_transaction():
        context.run_migrations()


def _resolve_database_url() -> str:
    """Return the database URL, preferring ``DEER_FLOW_DATABASE_URL``."""
    return os.environ.get(
        "DEER_FLOW_DATABASE_URL",
        config.get_main_option("sqlalchemy.url"),
    )


async def run_migrations_online() -> None:
    connectable = create_async_engine(_resolve_database_url())
    async with connectable.begin() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
