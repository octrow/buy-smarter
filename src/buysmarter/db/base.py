"""Async SQLAlchemy engine, session factory, and declarative base."""

from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from buysmarter.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def create_engine() -> AsyncEngine:
    """Create the async engine from settings.

    ``NullPool`` gives every session a fresh asyncpg connection that is closed
    on release rather than returned to a pool. The pipeline opens many short
    sessions in quick succession (city/knowledge lookups, per-candidate history,
    persistence); a pooled connection whose previous reset is still in flight
    was being handed straight to the next session, surfacing asyncpg's "cannot
    perform operation: another operation is in progress". A fresh connection per
    session removes that race entirely — throughput here is tiny, so the extra
    connect cost is irrelevant.
    """
    return create_async_engine(get_settings().database_url, echo=False, poolclass=NullPool)


engine: AsyncEngine = create_engine()

async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
)
