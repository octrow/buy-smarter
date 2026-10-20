import asyncio
from logging.config import fileConfig
from typing import Any

from sqlalchemy import create_engine, event, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from buysmarter.config import get_settings
from buysmarter.db.base import Base
from buysmarter.db import models as db_models  # noqa: F401  (register tables on Base.metadata)
from buysmarter.llm import models as llm_models  # noqa: F401  (register llm_cache table)

config = context.config

# ensure_schema() builds the Config in code with its own URL; the CLI/Docker path reads Settings.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# How long a second process waits for the one holding the migration lock.
LOCK_WAIT_S = 60


def run_migrations_offline() -> None:
    """Emit the migration SQL for the configured URL without connecting."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite: the whole upgrade runs in one BEGIN IMMEDIATE transaction (see run_sqlite).
        transactional_ddl=connection.dialect.name == "sqlite" or None,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_sqlite(url: str) -> None:
    """Sync SQLite path: BEGIN IMMEDIATE locks the file, so a second process waits, then sees head.

    The pysqlite recipe: autocommit at the driver level, and the transaction is begun by hand.
    """
    engine = create_engine(url, poolclass=pool.NullPool, connect_args={"timeout": LOCK_WAIT_S})

    @event.listens_for(engine, "connect")
    def _autocommit_driver(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn: Connection) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    with engine.connect() as connection:
        do_run_migrations(connection)
    engine.dispose()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url") or ""
    if url.startswith("sqlite"):
        run_sqlite(url.replace("sqlite+aiosqlite://", "sqlite://", 1))
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
