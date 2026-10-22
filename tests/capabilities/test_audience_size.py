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
        search_phrase="мужские кроссовки 42 размер", required_features=["42 размер"]
    )

    constraints = _read("мужские кроссовки 42 размер", parsed)

    assert constraints.gender == "male"
    assert constraints.size == "42"
    assert constraints.search_phrase == "мужские кроссовки"
    assert constraints.required_features == []


def test_a_known_bare_size_leaves_the_apparel_search_phrase() -> None:
    """Live 2026-10-22: «мужские кроссовки 42» kept "42" in the phrase, so the old
    "мужские-кроссовки" record was never moved under "кроссовки"."""
    parsed = QueryConstraints(search_phrase="мужские кроссовки 42", size="42")

    constraints = _read("мужские кроссовки 42", parsed)

    assert constraints.search_phrase == "мужские кроссовки"
    assert constraints.size == "42"


def test_a_bare_number_stays_without_a_known_size_or_outside_apparel() -> None:
    assert _read("", QueryConstraints(search_phrase="кроссовки 42")).search_phrase == (
        "кроссовки 42"
    )
    monitor = QueryConstraints(search_phrase="монитор 27", size="27")
    assert _read("", monitor).search_phrase == "монитор 27"
    shoes = QueryConstraints(search_phrase="кроссовки Nike Air 142", size="42")
    assert _read("", shoes).search_phrase == "кроссовки Nike Air 142"


def test_a_blank_gender_or_size_from_the_parser_counts_as_missing() -> None:
    """An LLM answering "" must not switch the regex reading off."""
    parsed = QueryConstraints(search_phrase="мужские кроссовки 42 размер", gender="", size="")

    constraints = _read("мужские кроссовки 42 размер", parsed)

    assert (constraints.gender, constraints.size) == ("male", "42")


def test_a_size_outside_apparel_is_a_spec_not_a_size() -> None:
    parsed = QueryConstraints(search_phrase="монитор размер 27", required_features=["размер 27"])

    constraints = _read("монитор размер 27", parsed)

    assert constraints.size is None
    assert constraints.required_features == ["размер 27"]


def test_audience_is_one_listing_check_for_every_pool() -> None:
    constraints = QueryConstraints(search_phrase="кроссовки", gender="male")
    active = activate(REGISTRY, _PROFILE, constraints, query="", use_llm=False)

    assert active.reject_listing("Кроссовки женские Carina") == "other audience: female"
    assert active.reject_listing("Кроссовки Smash мужские") is None
    assert active.reject_listing("Кроссовки RS-X") is None


def test_no_audience_in_the_query_switches_nothing_on() -> None:
    active = activate(
        REGISTRY, _PROFILE, QueryConstraints(search_phrase="кроссовки"), query="", use_llm=False
    )

    assert active.items == []
    assert active.reject_listing("Кроссовки женские") is None


def test_the_size_con_comes_from_the_size_capability() -> None:
    constraints = QueryConstraints(search_phrase="кроссовки", size="42")
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
