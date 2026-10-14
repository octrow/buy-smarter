"""Settings also read the user config dir, so an installed CLI (uv tool) has a place for keys."""

from pathlib import Path

from buysmarter.config import Settings


def test_env_files_user_config_first_then_local_dotenv_wins():
    files = Settings.model_config["env_file"]
    assert files == (Path.home() / ".config" / "buysmarter" / ".env", ".env")
