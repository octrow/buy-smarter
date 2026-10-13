"""User preferences: Postgres-primary, JSON-file fallback (one interface).

Preferences (default city, budget, excluded brands...) live in the Postgres
``UserPreference`` table when it's reachable; when it isn't, the same three
functions transparently fall back to a local JSON file so the MCP tools work
with no database at all. Override the file location with ``$BUYSMARTER_PREFS_FILE``
(tests point it at a tmp path).
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from buysmarter.db import repo
from buysmarter.db.base import async_session

logger = logging.getLogger(__name__)

_DEFAULT_PREFS_FILE = Path.home() / ".config" / "buysmarter" / "prefs.json"


def _prefs_file() -> Path:
    """Path to the JSON fallback store (``$BUYSMARTER_PREFS_FILE`` overrides)."""
    override = os.environ.get("BUYSMARTER_PREFS_FILE")
    return Path(override).expanduser() if override else _DEFAULT_PREFS_FILE


def _read_file() -> dict[str, Any]:
    path = _prefs_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Preferences file unreadable (%r); treating as empty", exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("Preferences file is not a JSON object; treating as empty")
        return {}
    return data


def _write_file(data: dict[str, Any]) -> None:
    path = _prefs_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def get_all() -> dict[str, Any]:
    """All preferences; Postgres if reachable, else the JSON fallback file."""
    try:
        async with async_session() as session:
            return await repo.get_all_preferences(session)
    except Exception as exc:  # noqa: BLE001 - DB is optional, fall back to file
        logger.warning("Preferences DB unavailable (%r); reading JSON fallback", exc)
        return _read_file()


async def get(key: str) -> Any | None:
    """One preference value, or ``None`` if unset (DB-primary, file fallback)."""
    try:
        async with async_session() as session:
            return await repo.get_preference(session, key)
    except Exception as exc:  # noqa: BLE001 - DB is optional, fall back to file
        logger.warning("Preferences DB unavailable (%r); reading JSON fallback", exc)
        return _read_file().get(key)


async def set(key: str, value: Any) -> None:
    """Persist a preference (DB-primary; JSON fallback when the DB is down)."""
    try:
        async with async_session() as session:
            await repo.set_preference(session, key, value)
            return
    except Exception as exc:  # noqa: BLE001 - DB is optional, fall back to file
        logger.warning("Preferences DB unavailable (%r); writing JSON fallback", exc)
    data = _read_file()
    data[key] = value
    _write_file(data)
