"""WB in the pool: its cards become candidates with their offer, in their own currency."""

from market_sources.base import RawOffer, RawProduct

from buysmarter.services.collect import gather_sources, merge_new_goods
from tests.fakes import FakeSource


async def test_wb_cards_join_the_pool_with_the_offer_their_search_carried():
    cards = [
        RawProduct(source="wb", product_id="1", title="Худи relaxed", price=15_600),
        RawProduct(source="wb", product_id="2", title="Чехол для телефона", price=900),
    ]
    offers = {"1": [RawOffer(source="wb", product_id="1", seller="AMAZING RED", price=15_600)]}
    wb = FakeSource("wb", cards, offer_map=offers)

    collected = await gather_sources({"wb": wb}, "худи", None, None, None)
    candidates, _rows = merge_new_goods(collected)

    assert [(c.source, c.title, c.offer_count, c.currency) for c in candidates] == [
        ("wb", "Худи relaxed", 1, "KZT")
    ]
