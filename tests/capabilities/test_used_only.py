"""Used only: "б/у ноутбук" brings in OLX and puts the used section first (a request capability)."""

from market_sources.base import RawProduct

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.flow import activate, pick_sources
from buysmarter.knowledge.profile import ResolvedProfile
from buysmarter.llm.prompts import parse_query_naive
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services.recommend import recommend
from tests.fakes import FakeSource

_PROFILE = ResolvedProfile(slug="x")


def _active(constraints: QueryConstraints) -> list[str]:
    active = activate(REGISTRY, _PROFILE, constraints, query="", use_llm=False)
    return [cap.name for cap, _ in active.items]


def test_the_parse_condition_switches_it_on():
    assert "used_only" in _active(QueryConstraints(condition="used"))
    assert "used_only" not in _active(QueryConstraints(condition="new"))
    assert "used_only" not in _active(QueryConstraints())


def test_it_adds_olx_once_and_only_for_a_used_query():
    used = QueryConstraints(condition="used")

    assert pick_sources(REGISTRY, _PROFILE, used, ["kaspi"]) == ["kaspi", "olx"]
    assert pick_sources(REGISTRY, _PROFILE, used, ["kaspi", "olx"]) == ["kaspi", "olx"]
    assert pick_sources(REGISTRY, _PROFILE, QueryConstraints(), ["kaspi"]) == ["kaspi"]


def test_the_naive_parse_of_b_u_reaches_it():
    assert parse_query_naive("б/у ноутбук").condition == "used"


def _rp(source: str, pid: str, title: str, price: float) -> RawProduct:
    return RawProduct(source=source, product_id=pid, title=title, price=price)


async def test_a_used_query_searches_olx_without_the_caller_naming_it():
    kaspi = FakeSource(
        "kaspi", [_rp("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", 30000)]
    )
    olx = FakeSource(
        "olx",
        [_rp("olx", "u1", "Waterpik WP-100 ирригатор портативный", 15000)],
        spec_map={"u1": {"condition": "Б/у", "city": "Алматы"}},
    )

    result = await recommend(
        "б/у ирригатор портативный",
        use_llm=False,
        source=kaspi,
        olx_source=olx,
        persist=False,
    )

    assert olx.searched
    assert [u.price for u in result.used_listings] == [15000]
    assert result.unavailable_sources == []
    assert any(c.name == "used_only" and c.active for c in result.capabilities)


async def test_a_new_query_does_not_search_olx():
    kaspi = FakeSource(
        "kaspi", [_rp("kaspi", "k1", "Waterpik WP-100 ирригатор портативный", 30000)]
    )
    olx = FakeSource("olx", [])

    await recommend(
        "ирригатор портативный", use_llm=False, source=kaspi, olx_source=olx, persist=False
    )

    assert not olx.searched
