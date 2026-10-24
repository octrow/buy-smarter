"""recommend() stops to learn a new category before any marketplace (#26, ADR-0006)."""

from typing import Any

import pytest

from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.profile import ResolvedProfile
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services import auto_learn
from buysmarter.services import recommend as recommend_module
from buysmarter.services.auto_learn import NeedsLearning
from tests.fakes import FakeSource

_NEW = QueryConstraints(intent="product", product_type="кофемолка", search_phrase="кофемолка")


@pytest.fixture
def source(monkeypatch) -> FakeSource:
    """An empty Kaspi with an LLM route and the claude CLI: the learning condition can hold."""

    async def _no_preference() -> str | None:
        return None

    monkeypatch.setattr(recommend_module, "_city_preference", _no_preference)
    monkeypatch.setattr(recommend_module, "_no_llm_route", lambda: False)
    monkeypatch.setattr(auto_learn, "claude_available", lambda: True)
    return FakeSource("kaspi", [])


def _profile(monkeypatch, profile: ResolvedProfile) -> None:
    async def _resolve(category: str, **_: Any) -> ResolvedProfile:
        return profile

    monkeypatch.setattr(recommend_module, "ensure_profile", _resolve)


async def _run(source: FakeSource, **kwargs: Any):
    return await recommend_module.recommend(
        "кофемолка", source=source, persist=False, parsed=_NEW, **kwargs
    )


async def test_a_new_category_stops_before_any_marketplace(monkeypatch, source):
    _profile(monkeypatch, ResolvedProfile(slug="кофемолка"))

    with pytest.raises(NeedsLearning) as caught:
        await _run(source, learn_first=True)

    assert caught.value.category == "кофемолка"
    assert source.searched == []


async def test_without_learn_first_the_search_goes_as_before(monkeypatch, source):
    _profile(monkeypatch, ResolvedProfile(slug="кофемолка"))

    with pytest.raises(ValueError):  # an empty marketplace: nothing to recommend
        await _run(source)

    assert source.searched != []


async def test_a_related_profile_with_books_is_not_waited_for(monkeypatch, source):
    # "крем от акне" → "средства-от-акне": the books are there, nothing to learn (#25 untouched)
    related = ResolvedProfile(
        slug="средства-от-акне",
        brand_book=BrandBook(tiers={"La Roche-Posay": "A"}),
        found="средства-от-акне",
    )
    _profile(monkeypatch, related)

    with pytest.raises(ValueError):  # searched, nothing found
        await _run(source, learn_first=True)

    assert source.searched != []
