"""Composition as a capability: when it is on, and what it hands the core."""

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.composition import INGREDIENTS_UNCHECKED, Composition
from buysmarter.capabilities.flow import activate
from buysmarter.knowledge.profile import COMPOSITION, CapabilitySetting, ResolvedProfile
from buysmarter.ranking.engine import rank
from buysmarter.ranking.models import Candidate, QueryConstraints
from tests.fakes import composition_profile, judging

_QUERY = QueryConstraints(search_phrase="крем с адапаленом", required_features=["0,1%"])


def _cand(pid: str, title: str, **kw) -> Candidate:
    return Candidate(source="kaspi", source_product_id=pid, title=title, price=5000, **kw)


def test_composition_is_off_without_an_ingredient_book() -> None:
    profile = ResolvedProfile(slug="x", capabilities={COMPOSITION: CapabilitySetting()})

    assert Composition().setting(profile, _QUERY) is None
    assert Composition().setting(composition_profile(), _QUERY) is not None


def test_jevs_composition_without_a_book_ranks_as_spec_driven() -> None:
    """Characterization scenario 1 (spec-driven): no weights, hooks or unchecked flag."""
    jev_yes = CapabilitySetting(set_by="jev")
    profile = ResolvedProfile(slug="гречка", capabilities={COMPOSITION: jev_yes})

    active = activate(REGISTRY, profile, _QUERY, query="", use_llm=False)

    assert judging(active) == []
    assert active.rank_hooks().weights is None
    pool = [_cand("kl", "Клензит гель адапален")]
    assert active.flags(rank(pool, _QUERY, hooks=active.rank_hooks())) == {}


def test_the_core_gets_substance_searches_and_title_hits_to_protect() -> None:
    active = activate(REGISTRY, composition_profile(), _QUERY, query="", use_llm=False)
    pool = [_cand("kl", "Клензит гель адапален"), _cand("bio", "Bioderma крем")]

    assert active.searches() == [("адапален", ["адапален"])]
    assert active.protected(pool) == {"kl"}
    assert active.rank_hooks().weights is not None


async def test_an_unchecked_ranked_candidate_raises_the_flag() -> None:
    active = activate(REGISTRY, composition_profile(), _QUERY, query="", use_llm=False)
    pool = [_cand("kl", "Клензит гель адапален"), _cand("bio", "Bioderma крем")]
    await active.enrich(pool)

    recommendation = rank(pool, _QUERY, hooks=active.rank_hooks())

    assert active.flags(recommendation) == {INGREDIENTS_UNCHECKED: True}


def test_spec_driven_profile_activates_nothing() -> None:
    active = activate(REGISTRY, ResolvedProfile(slug="x"), _QUERY, query="", use_llm=False)

    assert judging(active) == []
    assert active.searches() == []
