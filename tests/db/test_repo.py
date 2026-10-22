"""Repo tests on the suite's tmp SQLite store (tests/conftest.py), emptied per test."""

import pytest
from market_sources.base import RawProduct, RawReview
from sqlalchemy import text

from buysmarter.db import repo
from buysmarter.db.base import async_session


@pytest.fixture
async def session():
    async with async_session() as sess:
        yield sess


async def test_upsert_product_is_idempotent(session) -> None:
    raw = RawProduct(
        source="kaspi",
        product_id="test-idempotent-1",
        title="Irrigator IPX7",
        url="https://kaspi.kz/p/test-idempotent-1",
        rating=4.5,
        review_count=100,
    )

    first = await repo.upsert_product(session, raw)
    updated = raw.model_copy(update={"title": "Irrigator IPX7 (2026)", "review_count": 150})
    second = await repo.upsert_product(session, updated)

    assert first.id == second.id  # same row, not a duplicate
    assert second.title == "Irrigator IPX7 (2026)"
    assert second.review_count == 150

    count = await session.execute(
        text(
            "SELECT count(*) FROM products "
            "WHERE source = 'kaspi' AND source_product_id = 'test-idempotent-1'"
        )
    )
    assert count.scalar_one() == 1


async def test_preference_set_get_roundtrip(session) -> None:
    value = {"city": "Almaty", "budget": 40000}
    await repo.set_preference(session, "defaults", value)
    assert await repo.get_preference(session, "defaults") == value

    await repo.set_preference(session, "defaults", {"city": "Astana"})
    assert await repo.get_preference(session, "defaults") == {"city": "Astana"}


async def _make_product(session, product_id: str) -> int:
    raw = RawProduct(
        source="kaspi",
        product_id=product_id,
        title=f"Irrigator {product_id}",
        url=f"https://kaspi.kz/p/{product_id}",
    )
    product = await repo.upsert_product(session, raw)
    return product.id


async def test_snapshots_accumulate_and_history_aggregates(session) -> None:
    product_id = await _make_product(session, "test-snap-1")

    await repo.add_price_snapshot(session, product_id, min_price=30_000, offer_count=3)
    await repo.add_price_snapshot(session, product_id, min_price=20_000, offer_count=5)

    snapshots = await repo.list_snapshots(session, product_id)
    assert len(snapshots) >= 2

    latest = await repo.latest_snapshot(session, product_id)
    assert latest is not None and latest.min_price == 20_000

    hist = await repo.price_history(session, product_id, days=30)
    assert hist is not None
    avg, minimum, count = hist
    assert minimum == 20_000
    assert count >= 2
    assert 20_000 <= avg <= 30_000


async def test_price_history_none_without_snapshots(session) -> None:
    product_id = await _make_product(session, "test-snap-empty")
    assert await repo.price_history(session, product_id, days=30) is None


async def test_find_products_matches_title_and_url(session) -> None:
    raw = RawProduct(
        source="kaspi",
        product_id="test-find-xiaomi",
        title="Xiaomi F300 Irrigator",
        url="https://kaspi.kz/p/test-find-xiaomi",
    )
    await repo.upsert_product(session, raw)

    by_title = await repo.find_products(session, "F300 Irrigator")
    assert any(p.source_product_id == "test-find-xiaomi" for p in by_title)

    by_url = await repo.find_products(session, "test-find-xiaomi")
    assert any(p.source_product_id == "test-find-xiaomi" for p in by_url)


async def test_track_category_dedups_and_persists(session) -> None:
    await repo.set_preference(session, repo.TRACKED_CATEGORIES_KEY, [])

    await repo.track_category(session, "ирригатор")
    await repo.track_category(session, "робот-пылесос")
    tracked = await repo.track_category(session, "ирригатор")  # duplicate → moves to front

    assert tracked[0] == "ирригатор"
    assert tracked.count("ирригатор") == 1
    assert set(await repo.get_tracked_categories(session)) == {"ирригатор", "робот-пылесос"}


async def test_set_preferences_keeps_none_of_them_when_one_cannot_be_stored(session) -> None:
    with pytest.raises(Exception):  # noqa: B017 - the driver's error type is not the point
        await repo.set_preferences(session, {"city": "Astana", "budget": object()})
    await session.rollback()
    assert await repo.get_all_preferences(session) == {}


async def test_reviews_are_stored_for_their_product(session) -> None:
    product = await repo.upsert_product(
        session, RawProduct(source="kaspi", product_id="rev-1", title="Irrigator")
    )
    raws = [
        RawReview(source="kaspi", product_id="rev-1", author="Айгуль", rating=5, text="ok"),
        RawReview(source="kaspi", product_id="rev-1", rating=2, created_at="2026-10-01"),
    ]

    stored = await repo.add_reviews(session, product.id, raws)

    assert [(r.product_id, r.author, r.rating, r.date) for r in stored] == [
        (product.id, "Айгуль", 5, None),
        (product.id, None, 2, "2026-10-01"),
    ]


async def test_review_count_history_skips_unknown_counts_oldest_first(session) -> None:
    product = await repo.upsert_product(
        session, RawProduct(source="kaspi", product_id="rc-1", title="Irrigator")
    )
    for count in (10, None, 25):
        await repo.add_price_snapshot(session, product.id, 9_000, 1, review_count=count)

    history = await repo.review_count_history(session, product.id)

    assert [count for _, count in history] == [10, 25]
    assert history[0][0] <= history[1][0]
