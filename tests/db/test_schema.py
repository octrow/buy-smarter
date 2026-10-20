"""`schema_is_current`: what doctor's Storage row reports."""

import sqlite3

import pytest

from buysmarter.config import get_settings
from buysmarter.db.schema import schema_is_current


async def test_migrated_store_is_current() -> None:
    assert await schema_is_current(get_settings().database_url)


async def test_unmigrated_store_is_not_current(tmp_path) -> None:
    db = tmp_path / "old.db"
    sqlite3.connect(db).close()

    assert not await schema_is_current(f"sqlite+aiosqlite:///{db}")


async def test_unreachable_server_is_not_current() -> None:
    pytest.importorskip("asyncpg", reason="a Postgres URL needs the [server] extra")
    assert not await schema_is_current("postgresql+asyncpg://u:p@127.0.0.1:1/none")
