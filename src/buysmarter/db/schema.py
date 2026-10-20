"""Bring the local store to the current schema at process start.

Called at every entry point (CLI root callback, MCP, GUI) before an event loop exists: env.py runs
SQLite migrations synchronously, so there is no nested ``asyncio.run``. Postgres is migrated by an
explicit ``alembic upgrade head`` step (compose ``migrate``, Helm job), never here.
"""

import threading
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection, make_url

from buysmarter.config import get_settings
from buysmarter.db.base import connect_timeout, make_engine

# alembic's ``context`` is process-global, so threads take turns; BEGIN IMMEDIATE in env.py
# serialises separate processes.
_LOCK = threading.Lock()


def _config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", "buysmarter:migrations")
    return cfg


async def schema_is_current(url: str) -> bool:
    """True when the store answers and its schema is at head (what ``doctor`` calls ok)."""
    head = ScriptDirectory.from_config(_config()).get_current_head()

    def _revision(conn: Connection) -> str | None:
        return MigrationContext.configure(conn).get_current_revision()

    probe = make_engine(url, **connect_timeout(url))
    try:
        async with probe.connect() as conn:
            return await conn.run_sync(_revision) == head
    except Exception:  # noqa: BLE001 - any failure means "not usable"
        return False
    finally:
        await probe.dispose()


def ensure_schema(url: str | None = None) -> None:
    """Create or upgrade the SQLite store to head; a no-op for any other backend."""
    url = url or get_settings().database_url
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return
    if parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    cfg = _config()
    # Config interpolates %-signs; a path may contain them.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    with _LOCK:
        command.upgrade(cfg, "head")
