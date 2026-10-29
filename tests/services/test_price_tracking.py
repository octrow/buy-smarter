"""Price tracking on the suite's tmp SQLite store (tests/conftest.py); sources are faked."""

import pytest
from market_sources.base import RawOffer, RawProduct

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.services import price_tracking as pt


def _row(
    product_id: str, price: float | None, offer_prices: tuple[float, ...] = ()
) -> pt.ProductRow:
    title = f"Irrigator {product_id}"
    product = RawProduct(source="kaspi", product_id=product_id, title=title, price=price)
    offers = [RawOffer(source="kaspi", product_id=product_id, price=p) for p in offer_prices]
    return product, {}, offers


async def test_a_second_refresh_reports_each_price_move_against_the_last_snapshot() -> None:
    assert await pt._persist_refresh([_row("a", 10_000), _row("b", 5_000)], "irrigators") == []

    changes = await pt._persist_refresh(
        [_row("a", 12_000, offer_prices=(8_000,)), _row("b", 5_000)], "irrigators"
    )

    assert [(c.title, c.old_price, c.new_price, c.delta) for c in changes] == [
        ("Irrigator a", 10_000, 8_000, -2_000)  # the cheapest offer beats the card price
    ]
    async with async_session() as session:
        assert await repo.get_tracked_categories(session) == ["irrigators"]


def test_the_snapshot_is_the_candidates_price_not_a_cheaper_card_price() -> None:
    product, _, offers = _row("a", 7_000, offer_prices=(8_000, 9_000))

    # The candidate is priced at its cheapest offer, so the 30-day average must be too.
    assert pt.snapshot_values(product, offers) == (8_000, 2)


async def test_a_product_without_any_price_gets_no_snapshot() -> None:
    assert await pt._persist_refresh([_row("free", None)], "irrigators") == []
    history = await pt.product_history("free")
    assert [(h.title, h.snapshots) for h in history] == [("Irrigator free", [])]


async def test_specs_are_saved_with_the_product() -> None:
    product, _, offers = _row("spec", 9_000)

    await pt._persist_refresh([(product, {"IPX": "7"}, offers)], "irrigators")

    async with async_session() as session:
        [stored] = await repo.find_products(session, "Irrigator spec")
    assert stored.specs == {"IPX": "7"}


@pytest.fixture
def fake_sources(monkeypatch):
    """Stub the network slice of ``refresh_category``; record which sources got closed."""
    closed: list[str] = []

    async def no_city(*_args):
        return "Алматы", None

    async def close(src):
        closed.append(src)

    monkeypatch.setattr(pt, "resolve_sources", lambda *_args: {"kaspi": "kaspi-src"})
    monkeypatch.setattr(pt, "resolve_city", no_city)
    monkeypatch.setattr(pt, "aclose", close)
    return closed


def _serve(monkeypatch, rows: list[pt.ProductRow]) -> None:
    async def gather(*_args):
        return "collected"

    monkeypatch.setattr(pt, "gather_sources", gather)
    monkeypatch.setattr(pt, "merge_new_goods", lambda collected: ([], rows))


async def test_refresh_lists_the_five_biggest_drops_biggest_first(fake_sources, monkeypatch):
    ids = [f"p{i}" for i in range(7)]
    _serve(monkeypatch, [_row(i, 10_000) for i in ids])
    await pt.refresh_category("irrigators")
    drops = [_row(i, 10_000 - 100 * (n + 1)) for n, i in enumerate(ids[:6])]  # -100..-600
    _serve(monkeypatch, [*drops, _row("p6", 11_000)])  # p6 rises

    result = await pt.refresh_category("irrigators")

    assert (result.product_count, result.changed) == (7, 7)
    assert [d.delta for d in result.drops] == [-600, -500, -400, -300, -200]
    assert fake_sources == ["kaspi-src", "kaspi-src"]


async def test_refresh_closes_the_sources_when_collecting_fails(fake_sources, monkeypatch):
    async def broken(*_args):
        raise RuntimeError("kaspi is down")

    monkeypatch.setattr(pt, "gather_sources", broken)

    with pytest.raises(RuntimeError):
        await pt.refresh_category("irrigators")
    assert fake_sources == ["kaspi-src"]


async def test_a_failing_category_does_not_stop_the_others(monkeypatch):
    async with async_session() as session:
        for category in ("broken", "irrigators"):
            await repo.track_category(session, category)

    async def refresh(category):
        if category == "broken":
            raise RuntimeError("source error")
        return pt.CategoryRefresh(category=category, product_count=1, changed=0)

    monkeypatch.setattr(pt, "refresh_category", refresh)

    assert [r.category for r in await pt.refresh_tracked()] == ["irrigators"]


async def test_history_lists_each_snapshot_newest_first() -> None:
    await pt._persist_refresh([_row("h", 10_000, offer_prices=(9_000, 9_500))], "irrigators")
    await pt._persist_refresh([_row("h", 8_000)], "irrigators")

    [history] = await pt.product_history("Irrigator h")

    assert history.source == "kaspi"
    assert [(s.min_price, s.offer_count) for s in history.snapshots] == [(8_000, 0), (9_000, 2)]


async def test_history_is_empty_when_the_store_is_unreachable(monkeypatch) -> None:
    def unreachable():
        raise OSError("no store")

    monkeypatch.setattr(pt, "async_session", unreachable)

    assert await pt.product_history("anything") == []
