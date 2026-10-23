"""Application settings loaded from environment / .env."""

from pathlib import Path

from platformdirs import user_data_dir
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# A uv tool install has no project folder, so keys live here; a local .env (a clone) overrides it.
USER_ENV_FILE = Path.home() / ".config" / "buysmarter" / ".env"


def default_database_url() -> str:
    """The local store: one SQLite file in the per-user data dir (resolved per call, for XDG)."""
    return f"sqlite+aiosqlite:///{Path(user_data_dir('buysmarter')) / 'buysmarter.db'}"


class Settings(BaseSettings):
    """Runtime configuration. Reads from environment, then ./.env, then the user config .env."""

    model_config = SettingsConfigDict(
        env_file=(USER_ENV_FILE, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Postgres for server deployments: DATABASE_URL=postgresql+asyncpg://...
    database_url: str = Field(default_factory=default_database_url)
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

    # HTTP API and its worker. Prefixed env names: these are this app's own switches.
    # Marketplaces the API accepts; ozon only where the worker has Chromium (the browser image).
    api_sources: str = Field("kaspi,olx", validation_alias="BUYSMARTER_SOURCES")
    # Comma-separated keys for X-API-Key; empty = the API is open (local, kind).
    api_keys: str = Field("", validation_alias="BUYSMARTER_API_KEYS")
    rate_limit_jobs: int = Field(10, validation_alias="BUYSMARTER_RATE_LIMIT_JOBS")
    rate_limit_window_s: int = Field(600, validation_alias="BUYSMARTER_RATE_LIMIT_WINDOW_S")
    worker_metrics_port: int = Field(9000, validation_alias="BUYSMARTER_WORKER_METRICS_PORT")
    job_timeout_s: int = Field(300, validation_alias="BUYSMARTER_JOB_TIMEOUT_S")

    # Auto-learning of a new category (services.auto_learn): starts allowed per hour.
    # Off by default in the API/worker: it is open, the knowledge is shared and a learn is paid.
    auto_learn: bool = Field(False, validation_alias="BUYSMARTER_AUTO_LEARN")
    auto_learn_per_hour: int = Field(10, validation_alias="BUYSMARTER_AUTO_LEARN_PER_HOUR")


def get_settings() -> Settings:
    """Return a Settings instance (call site can cache if desired)."""
    return Settings()
