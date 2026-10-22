"""recommend() asks before it searches: clarifications, answers and rounds (phase 2, step 5)."""

from typing import Any

import pytest
from market_sources.base import RawProduct

from buysmarter.capabilities.base import Capability, CapabilityContext, Question
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services import recommend as recommend_module
from buysmarter.services.understanding import NeedsClarification, NothingToSearch
from tests.fakes import FakeSource


@pytest.fixture
def source(monkeypatch) -> FakeSource:
    async def _no_preference() -> str | None:
        return None

    monkeypatch.setattr(recommend_module, "_city_preference", _no_preference)
    irrigator = RawProduct(source="kaspi", product_id="1", title="Ирригатор Waterpik", price=25000)
    return FakeSource("kaspi", [irrigator])


@pytest.fixture
def profiles(monkeypatch) -> list[str]:
    """The keys ``resolve_profile`` was asked for (none stored)."""
    asked: list[str] = []

    async def _resolve(
        category: str, *, use_llm: bool = True, search_phrase: str | None = None
    ) -> ResolvedProfile:
        asked.append(f"{category} | {search_phrase}")
        return ResolvedProfile(slug=category)

    monkeypatch.setattr(recommend_module, "resolve_profile", _resolve)
    return asked


_GIFT = QueryConstraints(
    intent="open", options=["ирригатор", "плед"], budget_max=40000, search_phrase="подарок"
)


async def _run(source: FakeSource, query: str = "подарок маме", **kwargs: Any):
    return await recommend_module.recommend(
        query, use_llm=False, source=source, persist=False, **kwargs
    )


async def test_a_semantic_question_comes_before_the_profile_and_the_sources(source, profiles):
    with pytest.raises(NeedsClarification) as caught:
        await _run(source, parsed=_GIFT)

    assert caught.value.clarification.kind == "open"
    assert caught.value.clarification.options == ["ирригатор", "плед"]
    assert profiles == [] and source.searched == []


async def test_an_unreadable_query_without_llm_is_asked_to_rephrase(source, profiles) -> None:
    with pytest.raises(NeedsClarification) as caught:
        await _run(source, query="???")

    assert caught.value.clarification.kind == "unclear"


async def test_the_last_round_of_an_unreadable_query_has_nothing_to_search(source, profiles):
    with pytest.raises(NothingToSearch):
        await _run(source, query="???", clarify_round=2)

    assert source.searched == []


async def test_an_option_answer_searches_it_with_the_parsed_budget(source, profiles) -> None:
    result = await _run(source, parsed=_GIFT, answer="ирригатор", clarify_round=1)

    assert result.constraints.search_phrase == "ирригатор"
    assert result.constraints.budget_max == 40000
    assert result.assumed is None
    assert source.searched[0] == "ирригатор"


async def test_an_own_answer_is_added_to_the_query_and_parsed_again(source, profiles) -> None:
    result = await _run(source, query="подарок", parsed=_GIFT, answer="ирригатор до 30000 тг")

    assert result.constraints.intent == "product"
    assert result.constraints.search_phrase == "подарок ирригатор"
    assert result.constraints.budget_max == 30000


async def test_no_clarify_searches_the_first_option_and_says_so(source, profiles) -> None:
    result = await _run(source, parsed=_GIFT, no_clarify=True)

    assert result.assumed == "ирригатор"
    assert result.constraints.search_phrase == "ирригатор"


class _AsksForASize(Capability):
    name = "asks"

    def setting(self, profile: ResolvedProfile, constraints: QueryConstraints):
        return CapabilitySetting(set_by="fallback")

    def clarify(self, ctx: CapabilityContext) -> Question | None:
        return Question("Какой размер?", ["S", "M"])


async def test_a_capability_question_comes_after_the_profile_before_the_sources(
    source, profiles, monkeypatch
) -> None:
    monkeypatch.setattr(recommend_module, "REGISTRY", {"asks": _AsksForASize()})

    with pytest.raises(NeedsClarification) as caught:
        await _run(source, query="ирригатор до 40000 тг")

    assert caught.value.clarification.kind == "capability"
    assert profiles and source.searched == []
    result = await _run(source, query="ирригатор до 40000 тг", clarify_round=2)
    assert result.recommendation.best is not None


class _Histogram:
    def __init__(self) -> None:
        self.outcomes: list[str] = []

    def record(self, value: float, attributes: dict[str, str] | None = None) -> None:
        self.outcomes.append((attributes or {}).get("outcome", ""))


async def test_telemetry_tells_a_question_from_nothing_to_search(source, profiles, monkeypatch):
    histogram = _Histogram()
    monkeypatch.setattr(recommend_module.telemetry, "RECOMMEND_DURATION", histogram)

    with pytest.raises(NeedsClarification):
        await _run(source, query="???")
    with pytest.raises(NothingToSearch):
        await _run(source, query="???", no_clarify=True)

    assert histogram.outcomes == ["clarify", "nothing_to_search"]


async def test_the_profile_is_looked_up_by_the_product_type(source, profiles) -> None:
    parsed = QueryConstraints(search_phrase="мужские кроссовки", product_type="кроссовки")

    await _run(source, query="мужские кроссовки", parsed=parsed)

    assert profiles == ["кроссовки | мужские кроссовки"]
