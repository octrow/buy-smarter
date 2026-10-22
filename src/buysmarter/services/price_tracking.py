"""Price tracking: re-collect tracked categories, report the biggest drops, and
show one product's stored price history (the `refresh` and `history` commands).
"""

import asyncio
import logging

from pydantic import BaseModel, Field

from buysmarter.db.base import async_session
from buysmarter.db.repo import (
    add_offers,
    add_price_snapshot,
    find_products,
    get_tracked_categories,
    latest_snapshot,
    list_snapshots,
    track_category,
    upsert_product,
)
from buysmarter.services.collect import (
    ALL_SOURCES,
    ProductRow,
    aclose,
    gather_sources,
    merge_new_goods,
    resolve_sources,
    snapshot_values,
)
from buysmarter.services.recommend import resolve_city

logger = logging.getLogger(__name__)


# --- Price-tracking refresh (docs/PLAN.md фаза 4 "history цен") -------------
# `refresh` re-runs the search-only slice for every tracked category and records
# a fresh snapshot per product — no ranking, no LLM, sources scanned gently.

_REFRESH_TOP_DROPS = 5  # biggest drops surfaced per category summary


class PriceChange(BaseModel):
    """A product whose min price moved between the previous and new snapshot."""

    title: str
    old_price: int
    new_price: int
    delta: int  # new - old (negative == a price drop)


class CategoryRefresh(BaseModel):
    """Compact per-category outcome of a ``refresh`` run."""

    category: str
    product_count: int
    changed: int
    drops: list[PriceChange] = Field(default_factory=list)


async def _persist_refresh(rows: list[ProductRow], category: str) -> list[PriceChange]:
    """Persist rows + snapshots, returning the price changes vs the prior snapshot."""
    changes: list[PriceChange] = []
    async with async_session() as session:
        for product, specs, offers in rows:
            db_product = await upsert_product(session, product)
            if specs:
                db_product.specs = specs
                await session.commit()
            if offers:
                await add_offers(session, db_product.id, offers)
            snapshot = snapshot_values(product, offers)
            if snapshot is None:
                continue
            previous = await latest_snapshot(session, db_product.id)
            new_min, offer_count = snapshot
            await add_price_snapshot(
                session,
                db_product.id,
                new_min,
                offer_count,
                review_count=product.review_count,
                rating=product.rating,
            )
            if previous is not None and previous.min_price != new_min:
                changes.append(
                    PriceChange(
                        title=product.title,
                        old_price=previous.min_price,
                        new_price=new_min,
                        delta=new_min - previous.min_price,
                    )
                )
        await track_category(session, category)
    return changes


async def refresh_category(category: str) -> CategoryRefresh:
    """Search-only re-scan of one category: persist snapshots, report changes.

    All sources, naive (no brand-targeted searches, no ranking, no LLM). The
    persistence records a new snapshot per product and compares it to the prior
    one to surface price movements.
    """
    active = await asyncio.to_thread(resolve_sources, list(ALL_SOURCES), None, None, None)
    city, _ = await resolve_city(None, None)
    try:
        collected = await gather_sources(active, category, city, None, None)
        _, persist_rows = merge_new_goods(collected)
    finally:
        for src in active.values():
            await aclose(src)
    changes = await _persist_refresh(persist_rows, category) if persist_rows else []
    drops = sorted((c for c in changes if c.delta < 0), key=lambda c: c.delta)
    return CategoryRefresh(
        category=category,
        product_count=len(persist_rows),
        changed=len(changes),
        drops=drops[:_REFRESH_TOP_DROPS],
    )


async def refresh_tracked() -> list[CategoryRefresh]:
    """Refresh every tracked category, sequentially (gentle on the sources)."""
    async with async_session() as session:
        categories = await get_tracked_categories(session)
    results: list[CategoryRefresh] = []
    for category in categories:
        try:
            results.append(await refresh_category(category))
        except Exception as exc:  # noqa: BLE001 - one bad category must not abort refresh
            logger.warning("Refresh failed for %r: %r", category, exc, exc_info=True)
    return results


# --- Product price history (the `history` command) -------------------------


class SnapshotRow(BaseModel):
    """One captured price point for a product (rendered by ``history``)."""

    captured_at: str
    min_price: int
    offer_count: int


class ProductHistory(BaseModel):
    """A product and its snapshot timeline, newest first."""

    title: str
    source: str
    url: str | None = None
    snapshots: list[SnapshotRow] = Field(default_factory=list)


async def product_history(term: str) -> list[ProductHistory]:
    """Look up products by title/url substring and return their snapshot history.

    Best-effort — returns ``[]`` when Postgres is unreachable.
    """
    try:
        async with async_session() as session:
            products = await find_products(session, term)
            histories: list[ProductHistory] = []
            for product in products:
                snapshots = await list_snapshots(session, product.id)
                histories.append(
                    ProductHistory(
                        title=product.title,
                        source=product.source,
                        url=product.url,
                        snapshots=[
                            SnapshotRow(
                                captured_at=s.captured_at.isoformat(timespec="minutes"),
                                min_price=s.min_price,
                                offer_count=s.offer_count,
                            )
                            for s in snapshots
                        ],
                    )
                )
            return histories
    except Exception as exc:  # noqa: BLE001 - history is a read-only nicety
        logger.warning("Product-history lookup failed (DB unavailable?): %r", exc)
        return []
