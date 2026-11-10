"""Cross-market comparison: switched on by the query's words or the category, priced per source."""

import pytest
from market_sources.base import RawProduct

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.cross_market import CROSS_MARKET
from buysmarter.capabilities.flow import read_query
from buysmarter.llm.prompts import parse_query_naive
from buysmarter.services.recommend import recommend
from tests.fakes import FakeSource


@pytest.mark.parametrize(
    ("query", "on", "phrase"),
    [
        ("худи relaxed XL где дешевле", True, "худи relaxed XL"),
        ("джинсы Levi's 501 сравни площадки", True, "джинсы Levi's 501"),
        ("кроссовки ASICS из Китая", True, "кроссовки ASICS"),
        ("худи relaxed XL", False, "худи relaxed XL"),
    ],
)
def test_the_words_switch_it_on_and_leave_the_search(query, on, phrase):
    read = read_query(REGISTRY, parse_query_naive(query), query=query, use_llm=False)

    assert read.cross_market is on
    assert read.search_phrase == phrase


def test_jev_decides_it_for_a_category():
    assert REGISTRY[CROSS_MARKET].jev_question


def _source(name: str, *prices: int) -> FakeSource:
    cards = [
        RawProduct(source=name, product_id=f"{name}{i}", title=f"Худи oversize {i}", price=p)
        for i, p in enumerate(prices)
    ]
    return FakeSource(name, cards)


async def test_the_report_compares_each_marketplace_cheapest_to_the_door_first():
    result = await recommend(
        "худи где дешевле", use_llm=False, source=_source("kaspi", 25_000, 30_000), persist=False
    )

    assert [(r.offer.candidate.source, r.cost.total) for r in result.markets] == [
        ("kaspi", result.markets[0].offer.candidate.price)
    ]


async def test_without_the_capability_there_is_no_comparison():
    result = await recommend("худи", use_llm=False, source=_source("kaspi", 25_000), persist=False)

    assert result.markets == []
