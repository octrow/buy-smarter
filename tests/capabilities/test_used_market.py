"""Used market: the category switch that keeps OLX in or out of the sources."""

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.flow import pick_sources
from buysmarter.capabilities.provenance import (
    assessed_entries,
    capability_states,
    toggle_text,
)
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services.recommend import recommend
from tests.capabilities.test_used_only import _rp
from tests.fakes import FakeSource

_ALL = ["kaspi", "ozon", "olx"]
_OFF_BY_LEARNING = CapabilitySetting(enabled=False, set_by="learned")


def _profile(**settings: CapabilitySetting) -> ResolvedProfile:
    return ResolvedProfile(slug="x", capabilities=settings)


def _sources(profile: ResolvedProfile, constraints: QueryConstraints | None = None) -> list[str]:
    return pick_sources(REGISTRY, profile, constraints or QueryConstraints(), _ALL)


def test_it_is_on_by_default_and_changes_nothing():
    assert _sources(_profile()) == _ALL


def test_a_category_that_learned_it_off_loses_olx():
    assert _sources(_profile(used_market=_OFF_BY_LEARNING)) == ["kaspi", "ozon"]


def test_the_users_switch_outranks_learning_both_ways():
    off = CapabilitySetting(user=False)
    on = CapabilitySetting(enabled=False, set_by="learned", user=True)

    assert _sources(_profile(used_market=off)) == ["kaspi", "ozon"]
    assert _sources(_profile(used_market=on)) == _ALL


def test_it_never_adds_olx():
    assert pick_sources(REGISTRY, _profile(), QueryConstraints(), ["kaspi"]) == ["kaspi"]


def test_a_used_query_keeps_olx_whatever_the_category_switch():
    used = QueryConstraints(condition="used")

    assert _sources(_profile(used_market=_OFF_BY_LEARNING), used) == _ALL


def test_it_is_a_named_category_switch_without_a_jev_question():
    cap = REGISTRY["used_market"]

    assert (cap.title, cap.level, cap.jev_question) == ("Рынок б/у", "category", None)


def _state(profile: ResolvedProfile):
    states = {s.name: s for s in capability_states(REGISTRY, profile, QueryConstraints())}
    return states["used_market"]


def test_the_default_is_a_quiet_switch_that_the_report_line_leaves_out():
    state = _state(_profile())

    assert (state.on, state.set_by) == (True, "fallback")
    assert toggle_text(state) == "Рынок б/у — включено правилом"
    assert assessed_entries([state]) == []


def test_a_decision_shows_in_the_report_line():
    learned = _state(_profile(used_market=_OFF_BY_LEARNING))
    user = _state(_profile(used_market=CapabilitySetting(set_by=None, user=False)))

    assert (learned.on, learned.active) == (False, False)
    assert assessed_entries([learned]) == ["рынок б/у — выключено: обучение"]
    assert assessed_entries([user]) == ["рынок б/у — выключено вами"]


async def test_off_for_the_category_olx_is_not_searched_nor_reported_missing(monkeypatch):
    from buysmarter.services import recommend as module

    async def resolved(constraints, use_llm):
        return _profile(used_market=_OFF_BY_LEARNING)

    monkeypatch.setattr(module, "_resolve_knowledge", resolved)
    kaspi = FakeSource(
        "kaspi", [_rp("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", 30000)]
    )
    olx = FakeSource("olx", [_rp("olx", "u1", "Waterpik WP-100 ирригатор портативный", 15000)])

    result = await recommend(
        "ирригатор портативный",
        use_llm=False,
        sources=["kaspi", "olx"],
        source=kaspi,
        olx_source=olx,
        persist=False,
    )

    assert not olx.searched
    assert result.used_listings == []
    assert result.unavailable_sources == []
