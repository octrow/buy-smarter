"""Style: brands bring Kaspi searches, the «Стиль» axis ranks by the user's samples (plan В2–В6)."""

import asyncio

import pytest

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.base import CapabilityContext
from buysmarter.capabilities.flow import activate
from buysmarter.capabilities.style import NO_PHOTO, STYLE
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import Candidate, QueryConstraints
from buysmarter.style import profile
from buysmarter.style.profile import LIKE_PHOTO, LIKE_TEXT, NEVER_PHOTO, NEVER_TEXT

CAP = REGISTRY[STYLE]
ON = ResolvedProfile(slug="кроссовки", capabilities={STYLE: CapabilitySetting(set_by="jev")})
SNEAKERS = QueryConstraints(search_phrase="мужские кроссовки", product_type="кроссовки")
WEIGHTS = {STYLE: 20.0}


def _load() -> profile.Profile:
    return asyncio.run(profile.load())


def _ctx() -> CapabilityContext:
    return CapabilityContext(ON, SNEAKERS)


def _card(pid: str, **match: tuple[float, str]) -> Candidate:
    return Candidate(
        source="kaspi",
        source_product_id=pid,
        title=f"Кроссовки {pid}",
        price=30000,
        style_match=match,
    )


def _axis(pool: list[Candidate], card: Candidate):
    state = CAP.prepare(_ctx(), pool)
    return CAP.axis(_ctx(), state, card, WEIGHTS)


@pytest.fixture
def styled():
    """Brands and a phrase: something to compare photos with."""
    asyncio.run(profile.set_brands(["Salomon", "New Balance"]))
    asyncio.run(profile.add_phrase("technical shell jacket", never=False))
    return _load()


def test_it_is_a_category_switch_with_a_jev_question() -> None:
    assert (CAP.title, CAP.level, CAP.axis_name) == ("Стиль", "category", STYLE)
    assert CAP.jev_question and "clothing" in CAP.jev_question


def test_an_empty_style_profile_keeps_it_off_even_when_the_category_has_it() -> None:
    _load()

    assert CAP.setting(ON, SNEAKERS) is None


def test_a_category_without_it_stays_off_whatever_the_profile(styled) -> None:
    assert CAP.setting(ResolvedProfile(slug="ирригатор"), SNEAKERS) is None


def test_brands_become_kaspi_searches_that_must_name_the_brand(styled) -> None:
    active = activate(REGISTRY, ON, SNEAKERS, query="мужские кроссовки", use_llm=False)

    assert active.searches() == [
        ("кроссовки Salomon", ["Salomon"]),
        ("кроссовки New Balance", ["New Balance"]),
    ]


def test_at_most_six_brands_are_searched() -> None:
    asyncio.run(profile.set_brands([f"Brand{i}" for i in range(9)]))
    _load()

    assert len(CAP.searches(_ctx())) == 6


def test_the_closest_card_to_a_liked_photo_scores_highest_and_says_why(styled) -> None:
    near = _card("1", **{LIKE_PHOTO: (0.9, "look.jpg")})
    far = _card("2", **{LIKE_PHOTO: (0.4, "look.jpg")})
    mid = _card("3", **{LIKE_PHOTO: (0.6, "boots.jpg")})
    pool = [near, far, mid]

    scores = {c.source_product_id: _axis(pool, c) for c in pool}

    assert scores["1"].score > scores["3"].score > scores["2"].score
    assert scores["1"].max_score == 20.0
    assert "look.jpg" in scores["1"].explanation


def test_a_liked_phrase_counts_when_there_are_no_photos(styled) -> None:
    near = _card("1", **{LIKE_TEXT: (0.3, "technical shell jacket")})
    far = _card("2", **{LIKE_TEXT: (0.1, "technical shell jacket")})

    axis = _axis([near, far], near)

    assert axis.score > _axis([near, far], far).score
    assert "technical shell jacket" in axis.explanation


@pytest.mark.parametrize("component", [NEVER_PHOTO, NEVER_TEXT])
def test_a_card_close_to_a_never_sample_goes_down_and_says_so(styled, component) -> None:
    pool = [
        _card(str(i), **{LIKE_PHOTO: (0.7, "look.jpg"), component: (0.1, "glossy white sole")})
        for i in range(5)
    ]
    plastic = _card("p", **{LIKE_PHOTO: (0.7, "look.jpg"), component: (0.5, "glossy white sole")})
    pool.append(plastic)

    hit, clean = _axis(pool, plastic), _axis(pool, pool[0])

    assert hit.score < clean.score
    assert "нет: glossy white sole" in hit.explanation


def test_a_card_without_a_photo_gets_half_and_says_why(styled) -> None:
    seen = _card("1", **{LIKE_PHOTO: (0.9, "look.jpg")})
    blind = _card("2")

    axis = _axis([seen, blind], blind)

    assert axis.score == 10.0
    assert axis.explanation == NO_PHOTO


def test_strength_sets_the_axis_weight_and_zero_switches_it_off(styled) -> None:
    asyncio.run(profile.set_strength(35))
    _load()
    active = activate(REGISTRY, ON, SNEAKERS, query="кроссовки", use_llm=False)
    weights = active.rank_hooks().weights

    asyncio.run(profile.set_strength(0))
    _load()

    assert weights is not None
    assert weights[STYLE] == pytest.approx(35 * 100 / (100 + 35))
    assert CAP.setting(ON, SNEAKERS) is None


async def test_a_search_reads_the_profile_searches_the_brands_and_scores_style(monkeypatch) -> None:
    from buysmarter.services import recommend as pipeline
    from tests.capabilities.test_used_only import _rp
    from tests.fakes import FakeSource

    await profile.set_brands(["Salomon"])
    profile._current = profile.Profile()  # a fresh process: nothing loaded yet

    async def category(constraints, use_llm):
        return ON

    monkeypatch.setattr(pipeline, "_resolve_knowledge", category)
    kaspi = FakeSource(
        "kaspi",
        [_rp("kaspi", "1", "Кроссовки мужские белые", 20000)],
        by_query={"кроссовки Salomon": [_rp("kaspi", "2", "Кроссовки Salomon XT-6", 90000)]},
    )

    result = await pipeline.recommend(
        "мужские кроссовки", use_llm=False, source=kaspi, persist=False
    )

    assert "кроссовки Salomon" in kaspi.searched
    titles = [s.candidate.title for s in result.recommendation.ranked]
    assert "Кроссовки Salomon XT-6" in titles
    assert all(a.name != STYLE for s in result.recommendation.ranked for a in s.axes)  # brands only
