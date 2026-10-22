"""Minimal async persistence helpers (flat functions, not a class hierarchy).

Each function takes an ``AsyncSession`` and commits its own unit of work.
Input products/offers/reviews are the raw source-shaped Pydantic models from
``market_sources.base`` (or any object exposing the same attributes).
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from buysmarter.db.models import (
    CategoryKnowledge,
    Offer,
    PriceSnapshot,
    Product,
    Review,
    UserPreference,
)

# Single-user memory key holding the JSON list of recently queried categories.
TRACKED_CATEGORIES_KEY = "tracked_categories"
_TRACKED_CAP = 20
_HISTORY_DAYS = 30


async def upsert_product(session: AsyncSession, raw: Any) -> Product:
    """Insert or update a product keyed by ``(source, source_product_id)``."""
    stmt = select(Product).where(
        Product.source == raw.source,
        Product.source_product_id == raw.product_id,
    )
    product = (await session.execute(stmt)).scalar_one_or_none()
    if product is None:
        product = Product(source=raw.source, source_product_id=raw.product_id)
        session.add(product)

    product.title = raw.title
    product.brand = getattr(raw, "brand", None)
    product.category = getattr(raw, "category", None)
    product.url = raw.url or ""
    product.image_url = raw.image_url
    product.rating = raw.rating
    product.review_count = raw.review_count
    product.specs = getattr(raw, "specs", None)

    await session.commit()
    await session.refresh(product)
    return product


async def add_offers(session: AsyncSession, product_id: int, raws: Iterable[Any]) -> list[Offer]:
    """Append seller offers for a product; price is coerced to int KZT."""
    offers = [
        Offer(
            product_id=product_id,
            merchant_id=raw.merchant_id,
            merchant_name=raw.seller or "",
            price=int(raw.price),
            delivery_info={"delivery": raw.delivery, "city": raw.city, "in_stock": raw.in_stock},
        )
        for raw in raws
    ]
    session.add_all(offers)
    await session.commit()
    return offers


async def add_reviews(session: AsyncSession, product_id: int, raws: Iterable[Any]) -> list[Review]:
    """Append user reviews for a product."""
    reviews = [
        Review(
            product_id=product_id,
            author=raw.author,
            rating=raw.rating,
            text=raw.text,
            date=raw.created_at,
        )
        for raw in raws
    ]
    session.add_all(reviews)
    await session.commit()
    return reviews


async def add_price_snapshot(
    session: AsyncSession,
    product_id: int,
    min_price: int,
    offer_count: int,
    review_count: int | None = None,
    rating: float | None = None,
) -> PriceSnapshot:
    """Record a point-in-time price aggregate (plus review stats) for trend history."""
    snapshot = PriceSnapshot(
        product_id=product_id,
        min_price=min_price,
        offer_count=offer_count,
        review_count=review_count,
        rating=rating,
    )
    session.add(snapshot)
    await session.commit()
    await session.refresh(snapshot)
    return snapshot


async def get_product(session: AsyncSession, source: str, source_product_id: str) -> Product | None:
    """Look up a stored product by its ``(source, source_product_id)`` identity."""
    stmt = select(Product).where(
        Product.source == source,
        Product.source_product_id == source_product_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def latest_snapshot(session: AsyncSession, product_id: int) -> PriceSnapshot | None:
    """Return the most recently captured snapshot for a product, or ``None``."""
    stmt = (
        select(PriceSnapshot)
        .where(PriceSnapshot.product_id == product_id)
        .order_by(PriceSnapshot.captured_at.desc(), PriceSnapshot.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_snapshots(
    session: AsyncSession, product_id: int, limit: int = 50
) -> list[PriceSnapshot]:
    """Return a product's snapshots, newest first (for the ``history`` command)."""
    stmt = (
        select(PriceSnapshot)
        .where(PriceSnapshot.product_id == product_id)
        .order_by(PriceSnapshot.captured_at.desc(), PriceSnapshot.id.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def price_history(
    session: AsyncSession, product_id: int, days: int = _HISTORY_DAYS
) -> tuple[float, int, int] | None:
    """Return ``(avg, min, count)`` of ``min_price`` over the last ``days``, or ``None``.

    ``None`` when the product has no snapshots in the window (so the caller
    attaches no history rather than a zero-count aggregate).
    """
    since = datetime.now(UTC) - timedelta(days=days)
    stmt = select(
        func.avg(PriceSnapshot.min_price),
        func.min(PriceSnapshot.min_price),
        func.count(PriceSnapshot.id),
    ).where(PriceSnapshot.product_id == product_id, PriceSnapshot.captured_at >= since)
    avg, minimum, count = (await session.execute(stmt)).one()
    if not count:
        return None
    return float(avg), int(minimum), int(count)


async def find_products(session: AsyncSession, term: str, limit: int = 10) -> list[Product]:
    """Find products whose title or source url contains ``term`` (case-insensitive)."""
    like = f"%{term}%"
    stmt = (
        select(Product)
        .where(or_(Product.title.ilike(like), Product.url.ilike(like)))
        .order_by(Product.updated_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


def merge_tracked_categories(
    existing: list[str], category: str, cap: int = _TRACKED_CAP
) -> list[str]:
    """Prepend ``category`` (most recent first), drop exact duplicates, cap length.

    Pure helper so the dedup/cap policy is unit-testable without a database.
    """
    merged = [category] + [c for c in existing if c != category]
    return merged[:cap]


async def track_category(session: AsyncSession, category: str) -> list[str]:
    """Record a queried category into the ``tracked_categories`` preference list."""
    current = await get_preference(session, TRACKED_CATEGORIES_KEY) or []
    merged = merge_tracked_categories(current, category)
    await set_preference(session, TRACKED_CATEGORIES_KEY, merged)
    return merged


async def get_tracked_categories(session: AsyncSession) -> list[str]:
    """Return the tracked-category list (empty when unset)."""
    return await get_preference(session, TRACKED_CATEGORIES_KEY) or []


async def get_preference(session: AsyncSession, key: str) -> Any | None:
    """Return the stored value for ``key``, or ``None`` if unset."""
    stmt = select(UserPreference).where(UserPreference.key == key)
    pref = (await session.execute(stmt)).scalar_one_or_none()
    return pref.value if pref is not None else None


async def get_all_preferences(session: AsyncSession) -> dict[str, Any]:
    """Return every stored preference as a ``{key: value}`` mapping."""
    rows = (await session.execute(select(UserPreference))).scalars().all()
    return {row.key: row.value for row in rows}


async def set_preference(session: AsyncSession, key: str, value: Any) -> UserPreference:
    """Insert or update a single preference row."""
    (pref,) = await set_preferences(session, {key: value})
    return pref


async def set_preferences(session: AsyncSession, values: Mapping[str, Any]) -> list[UserPreference]:
    """Insert or update several preference rows in one transaction: all of them or none."""
    prefs: list[UserPreference] = []
    for key, value in values.items():
        stmt = select(UserPreference).where(UserPreference.key == key)
        pref = (await session.execute(stmt)).scalar_one_or_none()
        if pref is None:
            pref = UserPreference(key=key, value=value)
            session.add(pref)
        else:
            pref.value = value
        prefs.append(pref)
    await session.commit()
    for pref in prefs:
        await session.refresh(pref)
    return prefs


async def get_category_knowledge(session: AsyncSession, category: str) -> dict[str, Any] | None:
    """Return the stored schema JSON for ``category``, or ``None``."""
    stmt = select(CategoryKnowledge).where(CategoryKnowledge.category == category)
    row = (await session.execute(stmt)).scalar_one_or_none()
    return row.schema_json if row is not None else None


async def upsert_category_knowledge(
    session: AsyncSession, category: str, schema_json: dict[str, Any]
) -> CategoryKnowledge:
    """Insert or update the LLM-generated schema for a category."""
    stmt = select(CategoryKnowledge).where(CategoryKnowledge.category == category)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = CategoryKnowledge(category=category, schema_json=schema_json)
        session.add(row)
    else:
        row.schema_json = schema_json
    await session.commit()
    await session.refresh(row)
    return row


RecordEdit = Callable[[str, dict[str, Any]], dict[str, Any] | None]


async def rename_category_knowledge(
    session: AsyncSession, old: str, new: str, edit: RecordEdit
) -> bool:
    """Move the record ``old`` to ``new`` and apply ``edit`` to every record, in one transaction.

    ``edit(slug, payload)`` returns a record's new payload (the moved record's synonyms, a
    child's ``parent``) or ``None`` to leave it. False when ``old`` is gone (another writer
    was first); ``IntegrityError`` when ``new`` already exists. Any failure rolls it all back.
    """
    stmt = (
        update(CategoryKnowledge)
        .where(CategoryKnowledge.category == old)
        .values(category=new)
        .execution_options(synchronize_session=False)
    )
    try:
        result = await session.execute(stmt)
        if getattr(result, "rowcount", 0) != 1:
            await session.rollback()
            return False
        for row in (await session.execute(select(CategoryKnowledge))).scalars():
            if (payload := edit(row.category, dict(row.schema_json))) is not None:
                row.schema_json = payload
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return True


async def delete_category_knowledge(session: AsyncSession, category: str) -> None:
    """Remove the record ``category`` (no-op when it is gone); the caller commits.

    A rename deletes the bookless record it lands over in its own transaction, so a failed
    move rolls the delete back too.
    """
    await session.execute(delete(CategoryKnowledge).where(CategoryKnowledge.category == category))


async def list_category_slugs(session: AsyncSession) -> list[str]:
    """Return every stored ``CategoryKnowledge`` slug (for fallback category matching)."""
    stmt = select(CategoryKnowledge.category)
    return list((await session.execute(stmt)).scalars().all())


async def list_category_knowledge(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """Every stored ``CategoryKnowledge`` schema by slug (related-profile lookup)."""
    stmt = select(CategoryKnowledge.category, CategoryKnowledge.schema_json)
    return {slug: schema for slug, schema in (await session.execute(stmt)).all()}


async def review_count_history(
    session: AsyncSession, product_id: int
) -> list[tuple[datetime, int]]:
    """Return ``(captured_at, review_count)`` pairs with a known count, oldest first."""
    stmt = (
        select(PriceSnapshot.captured_at, PriceSnapshot.review_count)
        .where(
            PriceSnapshot.product_id == product_id,
            PriceSnapshot.review_count.is_not(None),
        )
        .order_by(PriceSnapshot.captured_at.asc())
    )
    return [(captured_at, count) for captured_at, count in (await session.execute(stmt)).all()]
