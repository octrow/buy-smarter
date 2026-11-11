"""User preferences (default city, budget, excluded brands...) in the local store.

One ``UserPreference`` row per key. Reads degrade to "nothing set" when the store is unavailable;
a write raises, so the caller never believes a preference was kept when it was not.

Earlier versions kept preferences in a JSON file (``$BUYSMARTER_PREFS_FILE``);
``import_legacy_file`` carries it over once at start.
"""

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.knowledge.seller_questions import Body
from buysmarter.local_store import set_aside
from buysmarter.ranking.landed_cost import DEFAULT_TARIFFS, Tariffs
from buysmarter.services.collect import ALL_SOURCES

logger = logging.getLogger(__name__)

# The keys the app, the CLI and MCP read and write.
CITY = "city"  # the core reads this one too (services.recommend.resolve_city)
BUDGET = "budget"  # and this one (services.recommend.stored_budget)
MARKETPLACES = "marketplaces"
EXCLUDED_BRANDS = "excluded_brands"
# The user's own costs for the landed cost of a foreign offer (``ranking.landed_cost.Tariffs``).
BANK_MARGIN = "bank_margin"  # the bank's mark-up over the National Bank rate, in percent
CARGO_RATE = "cargo_usd_per_kg"  # the user's cargo, USD per kg
# The user's body measurements (CONTEXT.md "Body measurements"): only the terminal and the app
# read them, to fill seller questions; never the core, a report file, MCP or the HTTP API.
HEIGHT = "height_cm"
BODY_WEIGHT = "weight_kg"
CHEST = "chest_cm"
WAIST = "waist_cm"
INSEAM = "inseam_cm"
# Each measurement's upper bound; 0 or unset means "not given".
BODY_TOPS = {HEIGHT: 250, BODY_WEIGHT: 300, CHEST: 200, WAIST: 200, INSEAM: 150}
BUDGET_MAX = 100_000_000
BANK_MARGIN_MAX = 20.0
CARGO_RATE_MAX = 50.0

_DEFAULT_PREFS_FILE = Path.home() / ".config" / "buysmarter" / "prefs.json"


def _legacy_file() -> Path:
    """The pre-store JSON file (``$BUYSMARTER_PREFS_FILE`` overrides)."""
    override = os.environ.get("BUYSMARTER_PREFS_FILE")
    return Path(override).expanduser() if override else _DEFAULT_PREFS_FILE


def as_list(value: Any) -> list[str]:
    """A stored list or comma-separated string as clean, non-empty items."""
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list):
        return []
    return [text for item in items if (text := str(item).strip())]


def as_budget(value: Any) -> int:
    """A stored budget (int or "40 000") in KZT; 0 means no limit."""
    digits = "".join(str(value or "").split())  # also drops the no-break space of "40 000"
    return min(int(digits), BUDGET_MAX) if digits.isdigit() else 0


def as_sources(value: Any) -> list[str]:
    """The known marketplaces of a stored list, once each, in the stored order; ``[]`` if none."""
    names = dict.fromkeys(name.lower() for name in as_list(value))
    return [name for name in names if name in ALL_SOURCES]


def as_number(value: Any, top: float) -> float | None:
    """A stored number ("2,5" too) within 0..``top``; ``None`` when unset or out of range."""
    try:
        number = float(str(value).replace(",", ".").strip())
    except ValueError:
        return None
    return number if 0 <= number <= top else None


def tariffs_of(stored: Mapping[str, Any]) -> Tariffs:
    """The saved bank mark-up and cargo rate, each falling back to the default when unset."""
    margin = as_number(stored.get(BANK_MARGIN), BANK_MARGIN_MAX)
    cargo = as_number(stored.get(CARGO_RATE), CARGO_RATE_MAX)
    return Tariffs(
        bank_margin=DEFAULT_TARIFFS.bank_margin if margin is None else margin / 100,
        cargo_usd_per_kg=DEFAULT_TARIFFS.cargo_usd_per_kg if cargo is None else cargo,
    )


def body_of(stored: Mapping[str, Any]) -> Body:
    """The saved measurements, a missing or out-of-range one left out."""
    values = {key: as_number(stored.get(key), top) for key, top in BODY_TOPS.items()}
    return Body(**{key: round(v) for key, v in values.items() if v})


async def search_defaults() -> tuple[list[str], list[str]]:
    """The saved marketplaces and excluded brands a search starts from (``[]``: none saved).

    The app's form and the CLI apply them; the core does not, so MCP and the HTTP API never
    inherit one user's settings.
    """
    stored = await get_all()
    return as_sources(stored.get(MARKETPLACES)), as_list(stored.get(EXCLUDED_BRANDS))


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


async def set_many(values: Mapping[str, Any]) -> None:
    """Persist several preferences at once, all or none; raises when the store cannot be written."""
    async with async_session() as session:
        await repo.set_preferences(session, values)


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
