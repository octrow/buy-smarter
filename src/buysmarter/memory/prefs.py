"""User preferences (default city, budget, excluded brands...) in the local store.

One ``UserPreference`` row per key. Reads degrade to "nothing set" when the store is unavailable;
a write raises, so the caller never believes a preference was kept when it was not.

Earlier versions kept preferences in a JSON file (``$BUYSMARTER_PREFS_FILE``);
``import_legacy_file`` carries it over once at start.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.local_store import set_aside

logger = logging.getLogger(__name__)

_DEFAULT_PREFS_FILE = Path.home() / ".config" / "buysmarter" / "prefs.json"


def _legacy_file() -> Path:
    """The pre-store JSON file (``$BUYSMARTER_PREFS_FILE`` overrides)."""
    override = os.environ.get("BUYSMARTER_PREFS_FILE")
    return Path(override).expanduser() if override else _DEFAULT_PREFS_FILE


async def get_all() -> dict[str, Any]:
    """All preferences, or ``{}`` when the store is unavailable."""
    try:
        async with async_session() as session:
            return await repo.get_all_preferences(session)
    except Exception as exc:  # noqa: BLE001 - the store is optional at read time
        logger.warning("Preferences store unavailable (%r); none applied", exc)
        return {}


async def get(key: str) -> Any | None:
    """One preference value, or ``None`` if unset or the store is unavailable."""
    try:
        async with async_session() as session:
            return await repo.get_preference(session, key)
    except Exception as exc:  # noqa: BLE001 - the store is optional at read time
        logger.warning("Preferences store unavailable (%r); %s not applied", exc, key)
        return None


async def set(key: str, value: Any) -> None:
    """Persist a preference; raises when the store cannot be written."""
    async with async_session() as session:
        await repo.set_preference(session, key, value)


async def import_legacy_file() -> None:
    """Import keys the store lacks from the old JSON file once, then rename it ``*.imported``.

    A corrupt or non-object file is renamed ``*.invalid`` with a warning. Re-running is safe:
    only missing keys are written.
    """
    path = _legacy_file()
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        data, reason = None, repr(exc)
    else:
        reason = "not a JSON object"
    if not isinstance(data, dict):
        logger.warning("Legacy preferences file %s is unreadable (%s); set aside", path, reason)
        set_aside(path, ".invalid")
        return
    async with async_session() as session:
        stored = await repo.get_all_preferences(session)
        for key, value in data.items():
            if key not in stored:
                await repo.set_preference(session, key, value)
    logger.info("Imported %d legacy preference(s) from %s", len(data.keys() - stored.keys()), path)
    set_aside(path, ".imported")
