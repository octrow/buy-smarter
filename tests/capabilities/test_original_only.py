"""Original only: suspected fakes are hidden, not shown with a warning (a request capability)."""

from typing import Any

import pytest
from market_sources.base import RawProduct

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.flow import read_query
from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.llm.prompts import parse_query_naive, parse_query_prompt
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services.recommend import NoNewPick, recommend
from tests.fakes import FakeSource


def _read(query: str, constraints: QueryConstraints | None = None) -> QueryConstraints:
    constraints = constraints or parse_query_naive(query)
    return read_query(REGISTRY, constraints, query=query, use_llm=False)


@pytest.mark.parametrize(
    ("query", "on", "phrase"),
    [
        ("пылесос Dyson только оригинал", True, "пылесос Dyson"),
        ("картридж HP 123 оригинал", True, "картридж HP 123"),
        ("оригинальный подарок", False, "оригинальный подарок"),
        ("пылесос Dyson", False, "пылесос Dyson"),
    ],
)
def test_original_only_is_read_and_leaves_the_search(query, on, phrase):
    read = _read(query)

    assert read.original_only is on
    assert read.search_phrase == phrase


def test_original_leaves_the_required_features():
    parsed = QueryConstraints(
        search_phrase="пылесос Dyson", required_features=["оригинал", "HEPA"], original_only=True
    )

    assert _read("пылесос Dyson оригинал", parsed).required_features == ["HEPA"]


def test_the_prompt_reads_original_only():
    assert '"original_only": true' in parse_query_prompt("пылесос")


async def _dyson_book() -> None:
    payload: dict[str, Any] = {
        "generated_by": "test",
        "updated_at": "2026-10-22T00:00:00+00:00",
        "brand_book": {"tiers": {"Dyson": "A"}},
        "model_book": {"entries": []},
    }
    async with async_session() as session:
        await repo.upsert_category_knowledge(session, "пылесос", payload)


def _kaspi(*titles: str) -> FakeSource:
    cards = [
        RawProduct(source="kaspi", product_id=str(i), title=t, price=100_000 + i, rating=4.8)
        for i, t in enumerate(titles)
    ]
    return FakeSource("kaspi", cards)


async def test_suspected_fakes_are_hidden_and_counted():
    await _dyson_book()
    kaspi = _kaspi("Пылесос Dyson V15", "Пылесос D yson V15 Detect", "Пылесос Dyson V12")

    result = await recommend("пылесос только оригинал", use_llm=False, source=kaspi, persist=False)

    titles = [s.candidate.title for s in result.recommendation.ranked]
    assert "Пылесос D yson V15 Detect" not in titles
    assert [s.candidate.title for s in result.hidden] == ["Пылесос D yson V15 Detect"]
    assert result.recommendation.rejected_reasons == {"suspected counterfeit": 1}
    assert result.recommendation.rejected_count == 1


async def test_without_original_only_a_fake_stays_with_its_warning():
    await _dyson_book()
    kaspi = _kaspi("Пылесос Dyson V15", "Пылесос D yson V15 Detect")

    result = await recommend("пылесос", use_llm=False, source=kaspi, persist=False)

    assert len(result.recommendation.ranked) == 2
    assert result.hidden == []


async def test_every_candidate_suspected_falls_through_to_olx():
    await _dyson_book()
    kaspi = _kaspi("Пылесос D yson V15", "Пылесос D yson V12")
    olx = FakeSource("olx", [])

    with pytest.raises(NoNewPick) as caught:
        await recommend(
            "пылесос только оригинал",
            use_llm=False,
            source=kaspi,
            olx_source=olx,
            sources=["kaspi", "olx"],
            persist=False,
        )

    assert caught.value.rejected_reasons == {"suspected counterfeit": 2}


def test_original_leaves_the_naive_product_type():
    assert _read("пылесос только оригинал").product_type == "пылесос"
