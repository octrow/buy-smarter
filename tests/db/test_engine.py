"""A DATABASE_URL whose driver is not installed turns the store off; it never stops the app."""

import logging

import pytest
from sqlalchemy.ext import asyncio as sa_asyncio

from buysmarter.db import base
from buysmarter.db.schema import schema_is_current

_PG_URL = "postgresql+asyncpg://u:p@127.0.0.1:5434/buysmarter"


@pytest.fixture
def no_asyncpg(monkeypatch):
    """The desktop build and a plain install: no asyncpg."""
    real = sa_asyncio.create_async_engine

    def create(url, **kwargs):
        if str(url).startswith("postgresql+asyncpg"):
            raise ModuleNotFoundError("No module named 'asyncpg'", name="asyncpg")
        return real(url, **kwargs)

    monkeypatch.setattr(base, "create_async_engine", create)


async def test_an_engine_without_its_driver_is_built_and_fails_only_when_used(no_asyncpg, caplog):
    with caplog.at_level(logging.WARNING):
        engine = base.make_engine(_PG_URL)
    assert "asyncpg" in caplog.text
    with pytest.raises(ModuleNotFoundError, match="asyncpg"):
        async with engine.connect():
            pass
    await engine.dispose()


async def test_the_store_reads_as_not_usable_without_its_driver(no_asyncpg):
    assert await schema_is_current(_PG_URL) is False
    assert await base.db_reachable(_PG_URL) is False
