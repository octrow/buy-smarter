"""A request with a size limit ("высотой до 50 см"): a wider, size-narrowed Kaspi pool.

Kaspi serves 12 cards a page and, without a login, two pages. A plain search for
"настольная посудомоечная машина" brings 12 of 121 matches, and the full-size
machines among them take the place of the cubes the user asked for. With a size
limit the search runs again with Kaspi's own size facet ("Высота: менее 75 см"),
both pages, so the pool holds 24 cards of the right size before the ranking
compares each card's spec with the limit.
"""

import logging
import re

from market_sources.base import ProductSource, RawProduct

from buysmarter.ranking.filters import size_limit, to_cm

logger = logging.getLogger(__name__)

KASPI_PAGES = 2  # pages Kaspi serves without a login
_ROW_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(мм|см|м)?\b", re.IGNORECASE)
_UP_TO = ("менее", "до", "меньше")
_FROM = ("более", "от", "больше")

SizeLimits = tuple[tuple[str, float], ...]


def size_limits(features: list[str]) -> SizeLimits:
    """The size limits among the required features, as (side stem, cm)."""
    return tuple(limit for f in features if (limit := size_limit(f)) is not None)


def _row_fits(title: str, limit_cm: float) -> bool:
    """Whether a facet row ("45 см", "менее 75 см", "более 90 см") may hold a size in the limit."""
    match = _ROW_RE.search(title)
    if match is None:
        return False
    size = to_cm(match.group(1), match.group(2) or "см")
    low = title.lower()
    if low.startswith(_UP_TO):
        return True  # a bucket from zero always overlaps the limit
    if low.startswith(_FROM):
        return size < limit_cm
    return size <= limit_cm


def kaspi_size_filters(facets: dict[str, list[tuple[str, str]]], limits: SizeLimits) -> str:
    """Kaspi facet row ids that keep the sizes within ``limits``; "" when no facet narrows."""
    parts: list[str] = []
    for side, limit_cm in limits:
        rows = next((rows for title, rows in facets.items() if title.lower().startswith(side)), [])
        keep = [row_id for row_id, title in rows if _row_fits(title, limit_cm)]
        if keep and len(keep) < len(rows):
            parts += keep
    return "".join(parts)


async def sized_kaspi_search(
    source: ProductSource, category: str, city_id: str | None, limits: SizeLimits
) -> list[RawProduct]:
    """Both Kaspi pages narrowed by the size facets the main search returned.

    Call after the main search (its facets are read). No facet that narrows → the
    plain second page. A failed page is logged and skipped.
    """
    facets_of = getattr(source, "facets", None)
    filters = kaspi_size_filters(facets_of(category, city_id), limits) if facets_of else ""
    pages = range(KASPI_PAGES) if filters else range(1, KASPI_PAGES)
    found: list[RawProduct] = []
    for page in pages:
        try:
            found += await source.search(category, city=city_id, page=page, filters=filters)  # type: ignore[call-arg]
        except Exception as exc:  # noqa: BLE001 - an extra page must not cost the main search
            logger.info("kaspi: sized search page %d failed: %r", page, exc)
    logger.info("kaspi: size limit %s, filters %r, %d more cards", limits, filters, len(found))
    return found
