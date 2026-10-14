"""Application settings loaded from environment / .env."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# A uv tool install has no project folder, so keys live here; a local .env (a clone) overrides it.
USER_ENV_FILE = Path.home() / ".config" / "buysmarter" / ".env"


class Settings(BaseSettings):
    """Runtime configuration. Reads from environment, then ./.env, then the user config .env."""

    model_config = SettingsConfigDict(
        env_file=(USER_ENV_FILE, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://buysmarter:buysmarter@localhost:5434/buysmarter"
    redis_url: str = "redis://localhost:6380/0"

    # LLM provider keys (optional): the primary provider is the local `claude`
    # CLI through OmniRoute (no key). These enable the API fallback only.
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    deepseek_api_key: str | None = None

    # LLM adapter defaults. `sonnet` routes to the local `claude` CLI.
    default_llm_model: str = "sonnet"
    prompt_version: str = "v1"

    # Mandatory local cache (sqlite, no Postgres/docker needed). Always used
    # unless a caller explicitly opts into the Postgres-backed SqlCacheStore.
    llm_cache_path: str = "~/.cache/buysmarter/llm_cache.db"
    # Kimovil phone specs by model query (services.phone_specs).
    kimovil_cache_path: str = "~/.cache/buysmarter/kimovil.db"

    # Kaspi.kz source (optional; datacenter IPs may need a residential proxy).
    kaspi_proxy: str | None = None

    # Ozon.kz source (Playwright browser session).
    ozon_base_url: str = "https://www.ozon.kz"


def get_settings() -> Settings:
    """Return a Settings instance (call site can cache if desired)."""
    return Settings()
