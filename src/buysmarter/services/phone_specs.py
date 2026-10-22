"""Kimovil specs for smartphone candidates.

Each candidate gets Kimovil's overall index for its model (``Candidate.benchmark_index``,
averaged into the specs axis) and the specs that do not depend on the memory variant,
in the wording Kaspi uses ("120 Гц", "5000 мАч"), so required features like these
match. RAM and storage are left out: a Kimovil page describes the base variant, the
listing may be another one.

Fails open: a lookup error leaves the candidate as it was. Hits and misses are cached
in sqlite by model query, so a repeat run makes no requests.
"""

import asyncio
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Protocol

from market_sources.kimovil.parse import KimovilPhone, model_query
from market_sources.kimovil.source import KimovilSource

from buysmarter.config import get_settings
from buysmarter.ranking.models import Candidate

logger = logging.getLogger(__name__)

MAX_LOOKUPS = 12  # distinct models looked up per run; each costs 2-6 paced requests
HIT_TTL_S = 30 * 24 * 3600  # the index drifts slowly as newer phones appear
MISS_TTL_S = 7 * 24 * 3600  # Kimovil may add the model, or the block may clear
MODEL_LABEL = "Модель (Kimovil)"
BENCHMARK_LABEL = "Kimovil"


class PhoneSpecSource(Protocol):
    async def lookup(self, title: str) -> KimovilPhone | None: ...

    async def aclose(self) -> None: ...


def russian_specs(phone: KimovilPhone) -> dict[str, str]:
    """Variant-independent specs under Russian labels, Kaspi's units."""
    specs = phone.specs
    values = " ".join(specs.values())
    found = {
        MODEL_LABEL: phone.full_name,
        "Тип экрана": specs.get("Type"),
        "Частота обновления экрана": _with_unit(
            _number(r"Refresh rate\s*(\d+)\s*Hz", values), "Гц"
        ),
        "Процессор": _before_bracket(specs.get("Model")),
        "AnTuTu v10": _antutu(specs.get("Score")),
        "Ёмкость аккумулятора": _with_unit(
            _number(r"(\d+)\s*mAh", specs.get("Battery capacity")), "мАч"
        ),
        "Быстрая зарядка": _with_unit(
            _number(r"(\d+(?:\.\d+)?)\s*W\b", specs.get("Fast charge")), "Вт"
        ),
        "Защита": specs.get("Resistance certificates (dust, water)"),
        "DxOMark": specs.get("DxOMark"),
    }
    return {label: value for label, value in found.items() if value}


def _number(pattern: str, text: str | None) -> int | None:
    match = re.search(pattern, text or "", re.IGNORECASE)
    return round(float(match.group(1))) if match else None


def _with_unit(number: int | None, unit: str) -> str | None:
    return f"{number} {unit}" if number is not None else None


def _before_bracket(text: str | None) -> str | None:
    return text.split("(")[0].strip() or None if text else None


def _antutu(text: str | None) -> str | None:
    # "1.521.862 • Antutu v10": thousands separators vary, the score has 6+ digits.
    match = re.search(r"\d{6,}", re.sub(r"[.,\s]", "", (text or "").split("•")[0]))
    return match.group(0) if match else None


class KimovilCache:
    """model query -> KimovilPhone JSON (or a miss), with an age per row."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or get_settings().kimovil_cache_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS kimovil "
                "(query TEXT PRIMARY KEY, phone TEXT, fetched_at REAL NOT NULL)"
            )

    def get(self, query: str) -> tuple[bool, KimovilPhone | None]:
        """(cached, phone): ``(False, None)`` for an unknown or expired query."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT phone, fetched_at FROM kimovil WHERE query = ?", (query,)
            ).fetchone()
        if row is None:
            return False, None
        phone_json, fetched_at = row
        ttl = HIT_TTL_S if phone_json else MISS_TTL_S
        if time.time() - fetched_at > ttl:
            return False, None
        return True, KimovilPhone.model_validate_json(phone_json) if phone_json else None

    def put(self, query: str, phone: KimovilPhone | None) -> None:
        payload = phone.model_dump_json() if phone else None
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kimovil (query, phone, fetched_at) VALUES (?, ?, ?)",
                (query, payload, time.time()),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


async def enrich_with_kimovil(
    candidates: list[Candidate],
    source: PhoneSpecSource | None = None,
    cache: KimovilCache | None = None,
) -> None:
    """Attach Kimovil's index and specs to every candidate whose model it knows.

    One lookup per distinct model query, at most :data:`MAX_LOOKUPS` uncached ones.
    The marketplace's own spec values win over Kimovil's on a shared label.
    """
    cache = cache or KimovilCache()
    by_query: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        if query := model_query(candidate.title):
            by_query.setdefault(query, []).append(candidate)
    phones = await _resolve_all(list(by_query), source, cache)
    for query, phone in phones.items():
        if phone is None:
            continue
        extra = russian_specs(phone)
        for candidate in by_query[query]:
            candidate.specs = {**extra, **candidate.specs}
            candidate.benchmark_index = phone.ki
            candidate.benchmark_label = BENCHMARK_LABEL
    logger.info(
        "kimovil: %d/%d models matched", sum(p is not None for p in phones.values()), len(phones)
    )


async def _resolve_all(
    queries: list[str], source: PhoneSpecSource | None, cache: KimovilCache
) -> dict[str, KimovilPhone | None]:
    """Cached results, plus a lookup of up to :data:`MAX_LOOKUPS` uncached queries."""
    phones, missing = _split_cached(queries, cache)
    if missing:
        phones.update(await _lookup_and_cache(missing[:MAX_LOOKUPS], source, cache))
    return phones


def _split_cached(
    queries: list[str], cache: KimovilCache
) -> tuple[dict[str, KimovilPhone | None], list[str]]:
    phones: dict[str, KimovilPhone | None] = {}
    missing = []
    for query in queries:
        cached, phone = cache.get(query)
        if cached:
            phones[query] = phone
        else:
            missing.append(query)
    return phones, missing


async def _lookup_and_cache(
    queries: list[str], source: PhoneSpecSource | None, cache: KimovilCache
) -> dict[str, KimovilPhone | None]:
    owned = source is None
    source = source or KimovilSource()
    try:
        results = await asyncio.gather(*(source.lookup(q) for q in queries), return_exceptions=True)
    finally:
        if owned:
            await source.aclose()
    phones: dict[str, KimovilPhone | None] = {}
    for query, result in zip(queries, results, strict=True):
        if isinstance(result, BaseException):
            # Not cached: a transient failure (a Cloudflare burst block) should retry next run.
            logger.warning("kimovil lookup failed for %r: %s", query, result)
            continue
        cache.put(query, result)
        phones[query] = result
    return phones


__all__ = ["KimovilCache", "enrich_with_kimovil", "russian_specs"]
