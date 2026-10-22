"""Audience and size as request capabilities: read from the query, used at their hook points."""

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.flow import activate, read_query
from buysmarter.knowledge.profile import ResolvedProfile
from buysmarter.ranking.engine import rank
from buysmarter.ranking.models import Candidate, QueryConstraints

_PROFILE = ResolvedProfile(slug="кроссовки")


def _read(query: str, constraints: QueryConstraints) -> QueryConstraints:
    return read_query(REGISTRY, constraints, query=query, use_llm=False)


def test_the_query_names_the_audience_and_the_size() -> None:
    parsed = QueryConstraints(
        category="мужские кроссовки 42 размер", required_features=["42 размер"]
    )

    constraints = _read("мужские кроссовки 42 размер", parsed)

    assert constraints.gender == "male"
    assert constraints.size == "42"
    assert constraints.category == "мужские кроссовки"
    assert constraints.required_features == []


def test_a_size_outside_apparel_is_a_spec_not_a_size() -> None:
    parsed = QueryConstraints(category="монитор размер 27", required_features=["размер 27"])

    constraints = _read("монитор размер 27", parsed)

    assert constraints.size is None
    assert constraints.required_features == ["размер 27"]


def test_audience_is_one_listing_check_for_every_pool() -> None:
    constraints = QueryConstraints(category="кроссовки", gender="male")
    active = activate(REGISTRY, _PROFILE, constraints, query="", use_llm=False)

    assert active.reject_listing("Кроссовки женские Carina") == "other audience: female"
    assert active.reject_listing("Кроссовки Smash мужские") is None
    assert active.reject_listing("Кроссовки RS-X") is None


def test_no_audience_in_the_query_switches_nothing_on() -> None:
    active = activate(
        REGISTRY, _PROFILE, QueryConstraints(category="кроссовки"), query="", use_llm=False
    )

    assert active.items == []
    assert active.reject_listing("Кроссовки женские") is None


def test_the_size_con_comes_from_the_size_capability() -> None:
    constraints = QueryConstraints(category="кроссовки", size="42")
    active = activate(REGISTRY, _PROFILE, constraints, query="", use_llm=False)
    pool = [
        Candidate(source="kaspi", source_product_id="a", title="Кроссовки Puma 42", price=1000),
        Candidate(source="kaspi", source_product_id="b", title="Кроссовки Nike", price=1000),
    ]

    ranked = {
        s.candidate.source_product_id: s
        for s in rank(pool, constraints, hooks=active.rank_hooks()).ranked
    }

    assert "размер 42: наличие не проверено" in ranked["b"].cons
    assert "размер 42: наличие не проверено" not in ranked["a"].cons
