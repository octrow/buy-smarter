"""Async SQLAlchemy engine, session factory, and declarative base."""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Dialect, NullPool, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

from buysmarter.config import get_settings

logger = logging.getLogger(__name__)

DB_TIMEOUT_S = 3
SQLITE_BUSY_TIMEOUT_MS = 5000


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# JSONB on Postgres (the DDL existing databases already have), plain JSON on SQLite.
JsonType = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC on every backend: SQLite drops the offset, so naive reads get UTC back."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utcnow() -> datetime:
    """Python-side default: microsecond precision where SQLite's CURRENT_TIMESTAMP has seconds."""
    return datetime.now(UTC)


def _unicode_lower(value: Any) -> Any:
    """SQLite's ``lower`` contract: NULL and non-text pass through unchanged."""
    return value.lower() if isinstance(value, str) else value


def _sqlite_on_connect(dbapi_conn: Any, _record: Any) -> None:
    """WAL + busy timeout for the GUI/MCP/CLI sharing one file, FKs for the CASCADEs, and a
    Unicode ``lower`` (SQLite's built-in folds ASCII only) so ``ilike`` matches "Ирригатор"."""
    dbapi_conn.create_function("lower", 1, _unicode_lower, deterministic=True)
    cursor = dbapi_conn.cursor()
    # busy_timeout first: switching a fresh file to WAL takes a lock another process may hold.
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(url: str, **kwargs: Any) -> AsyncEngine:
    """An async engine; SQLite ones get ``_sqlite_on_connect``.

    A URL whose driver is not installed (Postgres in the desktop build or a plain install: no
    asyncpg) gives an engine that fails on connect, so the store is off and the app still starts.
    """
    try:
        eng = create_async_engine(url, **kwargs)
    except ModuleNotFoundError as exc:
        logger.warning(
            "DATABASE_URL needs %s, which is not installed; the local store is off "
            "(unset DATABASE_URL for the default SQLite store, or install buysmarter[server])",
            exc.name,
        )
        return _engine_without_driver(exc)
    if eng.dialect.name == "sqlite":
        event.listen(eng.sync_engine, "connect", _sqlite_on_connect)
    return eng


def _engine_without_driver(missing: ModuleNotFoundError) -> AsyncEngine:
    async def fail() -> Any:
        raise missing

    return create_async_engine("sqlite+aiosqlite://", async_creator=fail, poolclass=NullPool)


def create_engine() -> AsyncEngine:
    """Create the async engine from settings.

    ``NullPool`` gives every session a fresh connection that is closed
    on release rather than returned to a pool. The pipeline opens many short
    sessions in quick succession (city/knowledge lookups, per-candidate history,
    persistence); a pooled connection whose previous reset is still in flight
    was being handed straight to the next session, surfacing asyncpg's "cannot
    perform operation: another operation is in progress". A fresh connection per
    session removes that race entirely — throughput here is tiny, so the extra
    connect cost is irrelevant.
    """
    return make_engine(get_settings().database_url, echo=False, poolclass=NullPool)


def connect_timeout(url: str) -> dict[str, Any]:
    """asyncpg's connect timeout for a probe; sqlite3's ``timeout`` is a lock wait, not needed."""
    if url.startswith("postgresql+asyncpg"):
        return {"connect_args": {"timeout": DB_TIMEOUT_S}}
    return {}


async def db_reachable(url: str) -> bool:
    """True when ``select 1`` succeeds within ``DB_TIMEOUT_S``; throwaway engine, no pool state."""
    probe = make_engine(url, **connect_timeout(url))
    try:
        async with probe.connect() as conn:
            await conn.execute(text("select 1"))
        return True
    except Exception:
        return False
    finally:
        await probe.dispose()


engine: AsyncEngine = create_engine()

async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
)
