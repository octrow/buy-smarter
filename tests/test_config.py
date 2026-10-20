"""Settings also read the user config dir, so an installed CLI (uv tool) has a place for keys."""

from pathlib import Path

from buysmarter.config import Settings


def test_env_files_user_config_first_then_local_dotenv_wins():
    files = Settings.model_config["env_file"]
    assert files == (Path.home() / ".config" / "buysmarter" / ".env", ".env")


def test_default_database_url_is_a_sqlite_file_in_the_user_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    url = Settings(_env_file=None).database_url

    assert url == f"sqlite+aiosqlite:///{tmp_path / 'buysmarter' / 'buysmarter.db'}"


def test_database_url_env_overrides_the_default(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db/x")

    assert Settings(_env_file=None).database_url == "postgresql+asyncpg://u:p@db/x"


def test_suite_never_points_at_the_real_data_dir():
    from buysmarter.config import get_settings

    assert "buysmarter-tests-" in get_settings().database_url
