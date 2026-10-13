"""FileCacheStore (sqlite) roundtrip + default-store resolution tests."""

import importlib

from buysmarter.llm import cache as cache_module
from buysmarter.llm.cache import FileCacheStore, cache_key_for
from buysmarter.llm.models import LlmCache
from buysmarter.ranking.models import QueryConstraints


def _row(cache_key: str, model: str = "haiku", prompt_text: str | None = "find one") -> LlmCache:
    return LlmCache(
        provider="claude-cli",
        model=model,
        prompt_version="v1",
        prompt_hash="hash",
        prompt_text=prompt_text,
        input_hash="input-hash",
        cache_key=cache_key,
        response_text="raw text",
        structured_json={"category": "irrigator", "city": "Almaty"},
        cost_usd=0.002,
        latency_ms=42,
    )


async def test_file_cache_store_roundtrip(tmp_path) -> None:
    store = FileCacheStore(path=str(tmp_path / "llm_cache.db"))
    key = cache_key_for("haiku", "v1", "find one", QueryConstraints)

    assert await store.get(key) is None
    await store.put(_row(key))

    cached = await store.get(key)
    assert cached == {"category": "irrigator", "city": "Almaty"}

    with store._connect() as conn:
        row = conn.execute(
            "SELECT prompt_text FROM llm_cache WHERE cache_key = ?", (key,)
        ).fetchone()
    assert row[0] == "find one"


async def test_file_cache_store_migrates_old_schema_without_prompt_text(tmp_path) -> None:
    """A db created before the prompt_text column existed should gain it on open."""
    import sqlite3

    db_path = tmp_path / "old_llm_cache.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE llm_cache (
                cache_key TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                prompt_hash TEXT NOT NULL,
                response_text TEXT NOT NULL,
                structured_json TEXT NOT NULL,
                cost_usd REAL,
                latency_ms INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        old_key = cache_key_for("haiku", "v1", "old prompt", QueryConstraints)
        conn.execute(
            "INSERT INTO llm_cache (cache_key, provider, model, prompt_version, prompt_hash, "
            "response_text, structured_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (old_key, "claude-cli", "haiku", "v1", "hash", "raw text", '{"category": "old"}'),
        )
        conn.commit()

    # Opening with the new store must not crash, and the old row keeps NULL prompt_text.
    store = FileCacheStore(path=str(db_path))
    with store._connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_cache)").fetchall()}
        assert "prompt_text" in columns
        old_row = conn.execute(
            "SELECT prompt_text FROM llm_cache WHERE cache_key = ?", (old_key,)
        ).fetchone()
        assert old_row[0] is None

    # New writes through the migrated store carry prompt_text.
    new_key = cache_key_for("haiku", "v1", "new prompt", QueryConstraints)
    await store.put(_row(new_key, prompt_text="new prompt full text"))
    with store._connect() as conn:
        new_row = conn.execute(
            "SELECT prompt_text FROM llm_cache WHERE cache_key = ?", (new_key,)
        ).fetchone()
    assert new_row[0] == "new prompt full text"


async def test_file_cache_store_distinct_keys(tmp_path) -> None:
    store = FileCacheStore(path=str(tmp_path / "llm_cache.db"))
    key_a = cache_key_for("haiku", "v1", "prompt a", QueryConstraints)
    key_b = cache_key_for("haiku", "v1", "prompt b", QueryConstraints)

    await store.put(_row(key_a))
    await store.put(_row(key_b))

    assert await store.get(key_a) is not None
    assert await store.get(key_b) is not None
    assert await store.get(key_a) == await store.get(key_b)  # same payload, distinct rows
    assert key_a != key_b


def test_default_store_is_file_cache_store_without_explicit_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_CACHE_PATH", str(tmp_path / "default_cache.db"))
    reloaded = importlib.reload(cache_module)
    try:
        assert isinstance(reloaded._store, reloaded.FileCacheStore)
    finally:
        importlib.reload(cache_module)  # restore normal module state for later tests
