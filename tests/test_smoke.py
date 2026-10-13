"""Smoke test: package imports, Settings loads, CLI app exists."""

import typer

import buysmarter
from buysmarter.cli.main import app
from buysmarter.config import Settings


def test_package_version() -> None:
    assert buysmarter.__version__


def test_settings_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5433/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6380/0")
    settings = Settings(_env_file=None)
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.redis_url.startswith("redis://")


def test_cli_app_exists() -> None:
    assert isinstance(app, typer.Typer)
