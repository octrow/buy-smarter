"""The National Bank of Kazakhstan's daily rates, for the landed cost of a foreign offer.

The bank's RSS feed gives tenge per ``quant`` units of each currency. A day's rates are kept in
the local store (one ``UserPreference`` row): one fetch a day, and the last saved rates when
the feed is down. No rates at all leaves foreign offers out of the comparison, never guessed.
"""

import logging
import xml.etree.ElementTree as ET  # noqa: S405 - expat >= 2.4 refuses entity expansion bombs
from datetime import date, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from buysmarter.db import repo
from buysmarter.db.base import async_session

logger = logging.getLogger(__name__)

FEED_URL = "https://nationalbank.kz/rss/get_rates.cfm?fdate={day:%d.%m.%Y}"
CACHE_KEY = "fx_rates"
_TIMEOUT_S = 15.0


class Rates(BaseModel, frozen=True):
    """Tenge per one unit of each currency, as the bank set them for ``day``."""

    day: date
    kzt: dict[str, float]


def parse_rates(xml: str) -> Rates:
    """The feed's rates per one unit; an item with a broken number is skipped."""
    root = ET.fromstring(xml)  # noqa: S314 - see the import
    day = datetime.strptime(root.findtext("date", ""), "%d.%m.%Y").date()
    kzt: dict[str, float] = {}
    for item in root.iter("item"):
        try:
            rate = float(item.findtext("description", "")) / float(item.findtext("quant", "1"))
        except ValueError:
            continue
        kzt[item.findtext("title", "").strip()] = round(rate, 4)
    return Rates(day=day, kzt=kzt)


async def _cached() -> Any:
    async with async_session() as session:
        return await repo.get_preference(session, CACHE_KEY)


async def _cache(value: Any) -> None:
    async with async_session() as session:
        await repo.set_preference(session, CACHE_KEY, value)


async def _saved() -> Rates | None:
    try:
        return Rates.model_validate(await _cached() or {})
    except ValidationError:
        return None
    except Exception as exc:  # noqa: BLE001 - the store is optional at read time
        logger.warning("Saved rates unavailable (%r)", exc)
        return None


async def _fetch(day: date, client: httpx.AsyncClient) -> Rates:
    response = await client.get(FEED_URL.format(day=day), timeout=_TIMEOUT_S)
    response.raise_for_status()
    return parse_rates(response.text)


async def current_rates(today: date, client: httpx.AsyncClient | None = None) -> Rates | None:
    """Today's rates: from the store when fetched today, else from the feed, else the last saved."""
    saved = await _saved()
    if saved is not None and saved.day >= today:
        return saved
    try:
        if client is None:
            async with httpx.AsyncClient() as own:
                rates = await _fetch(today, own)
        else:
            rates = await _fetch(today, client)
    except (httpx.HTTPError, ET.ParseError, ValueError) as exc:
        logger.warning("National Bank rates unavailable (%r); using %s", exc, saved and saved.day)
        return saved
    try:
        await _cache(rates.model_dump(mode="json"))
    except Exception as exc:  # noqa: BLE001 - a dead store costs one more fetch, nothing else
        logger.warning("Rates not saved (%r)", exc)
    return rates
