"""Mandatory LLM response cache: key derivation and a pluggable store.

The default store is :class:`FileCacheStore`, a stdlib-sqlite3 file at
``settings.llm_cache_path`` (``~/.cache/buysmarter/llm_cache.db`` by default).
It needs no docker/Postgres, so caching never silently no-ops when the
database is down. ``SqlCacheStore`` (Postgres, via the async session factory)
is available for callers that explicitly opt in with :func:`set_store`. Tests
swap in the in-memory ``DictCacheStore`` the same way.

Key derivation comes from the shared ``claude_cli`` library
(:func:`claude_cli.cache_key`, itself over ``claude_cli.normalize_prompt``). The
stores themselves stay local: they are ``async`` and persist a whole pydantic-validated
``LlmCache`` row (structured JSON + provider/version/token/latency columns),
whereas the library's stores are synchronous ``key -> text``.
"""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Protocol

from claude_cli import cache_key
from pydantic import BaseModel
from sqlalchemy import delete, select

from buysmarter.config import get_settings
from buysmarter.db.base import async_session
from buysmarter.llm.models import LlmCache


def hash_text(text: str) -> str:
    """Return the hex sha256 of ``text`` (used for the row's audit hashes)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_key_for(
    model: str,
    prompt_version: str,
    prompt: str,
    response_model: type[BaseModel],
    cli_tools: str = "",
) -> str:
    """Project-shaped wrapper over :func:`claude_cli.cache_key`.

    Maps buy-smarter's pydantic-typed signature onto the library's labeled
    dimensions: the response model's class name is the ``schema`` dimension and
    ``prompt_version`` is ``version``. ``cli_tools`` is folded in only when
    non-empty — a tools-on call (web-search-grounded) must not share a cache row
    with a tools-off call using the identical prompt.
    """
    key: str = cache_key(  # claude_cli ships no type hints
        model,
        prompt,
        schema=response_model.__name__,
        version=prompt_version,
        tools=cli_tools,
    )
    return key


class CacheStore(Protocol):
    """Minimal interface the adapter needs from a cache backend."""

    async def get(self, cache_key: str) -> dict[str, Any] | None: ...

    async def put(self, row: LlmCache) -> None: ...


class SqlCacheStore:
    """Default store backed by the async SQLAlchemy session factory."""

    async def get(self, cache_key: str) -> dict[str, Any] | None:
        async with async_session() as session:
            result = await session.execute(select(LlmCache).where(LlmCache.cache_key == cache_key))
            row = result.scalar_one_or_none()
            return row.structured_json if row is not None else None

    async def put(self, row: LlmCache) -> None:
        async with async_session() as session:
            # Stale rows (schema drift) are re-generated under the same unique key: replace.
            await session.execute(delete(LlmCache).where(LlmCache.cache_key == row.cache_key))
            session.add(row)
            await session.commit()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    prompt_text TEXT,
    response_text TEXT NOT NULL,
    structured_json TEXT NOT NULL,
    cost_usd REAL,
    latency_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


class FileCacheStore:
    """Default store: a local sqlite3 file, no Postgres/docker required.

    sqlite3 calls are synchronous, but they cost microseconds against a local
    file, so the async interface just calls them inline rather than pulling in
    aiosqlite or an executor thread.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or get_settings().llm_cache_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            self._migrate(conn)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add ``prompt_text`` to a pre-existing db that predates this column."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_cache)").fetchall()}
        if "prompt_text" not in columns:
            conn.execute("ALTER TABLE llm_cache ADD COLUMN prompt_text TEXT")
            conn.commit()

    async def get(self, cache_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT structured_json FROM llm_cache WHERE cache_key = ?", (cache_key,)
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    async def put(self, row: LlmCache) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache "
                "(cache_key, provider, model, prompt_version, prompt_hash, prompt_text, "
                "response_text, structured_json, cost_usd, latency_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row.cache_key,
                    row.provider,
                    row.model,
                    row.prompt_version,
                    row.prompt_hash,
                    row.prompt_text,
                    row.response_text,
                    json.dumps(row.structured_json),
                    row.cost_usd,
                    row.latency_ms,
                ),
            )
            conn.commit()


class DictCacheStore:
    """In-memory store for tests; keyed by ``cache_key``."""

    def __init__(self) -> None:
        self.rows: dict[str, LlmCache] = {}

    async def get(self, cache_key: str) -> dict[str, Any] | None:
        row = self.rows.get(cache_key)
        return row.structured_json if row is not None else None

    async def put(self, row: LlmCache) -> None:
        self.rows[row.cache_key] = row


_store: CacheStore = FileCacheStore()


def set_store(store: CacheStore) -> None:
    """Replace the active cache store (used by tests)."""
    global _store
    _store = store


async def get_cached(cache_key: str) -> dict[str, Any] | None:
    """Return the cached structured JSON for ``cache_key``, or ``None``."""
    return await _store.get(cache_key)


async def store_cached(row: LlmCache) -> None:
    """Persist a cache row through the active store."""
    await _store.put(row)
