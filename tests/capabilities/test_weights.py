"""The weight rule: base + capability axes and shifts, clamped at 0, normalized to 100."""

import pytest

from buysmarter.capabilities.base import Capability, overrides
from buysmarter.capabilities.composition import Composition
from buysmarter.capabilities.weights import combine
from buysmarter.ranking.engine import DEFAULT_WEIGHTS

# The composition table before capabilities (ranking/engine.py COMPOSITION_WEIGHTS).
_OLD_COMPOSITION_WEIGHTS = {
    "ingredient": 25.0,
    "price": 25.0,
    "specs": 15.0,
    "brand": 10.0,
    "reviews": 12.0,
    "popularity": 3.0,
    "availability": 10.0,
}


class _Durability(Capability):
    name = "durability"
    axis_name = "durability"
    axis_weight = 10.0
    weight_shifts = {"popularity": -20.0}


def test_composition_numbers_reproduce_the_old_composition_table() -> None:
    assert combine(DEFAULT_WEIGHTS, [Composition()]) == _OLD_COMPOSITION_WEIGHTS


def test_two_capabilities_with_shifts_clamp_at_zero_and_sum_to_100() -> None:
    weights = combine(DEFAULT_WEIGHTS, [Composition(), _Durability()])

    assert weights["popularity"] == 0.0
    assert sum(weights.values()) == pytest.approx(100.0)
    # 100 base + 25 + 10 - 15 - 5 - 8 (popularity clamped) = 107 before normalizing
    assert weights["durability"] == pytest.approx(10.0 * 100 / 107)


def test_no_capabilities_keep_the_base_weights() -> None:
    assert combine(DEFAULT_WEIGHTS, []) == DEFAULT_WEIGHTS


def test_the_core_sees_only_the_hooks_a_capability_overrides() -> None:
    class _Searching(Capability):
        def searches(self, ctx):
            return [("адапален", ["адапален"])]

    assert overrides(_Searching(), "searches")
    assert not overrides(_Searching(), "enrich")


async def test_every_hook_of_a_bare_capability_is_neutral() -> None:
    from buysmarter.capabilities.base import CapabilityContext
    from buysmarter.knowledge.profile import ResolvedProfile
    from buysmarter.ranking.models import Candidate, QueryConstraints, Recommendation

    bare = Capability()
    constraints = QueryConstraints(search_phrase="ноутбук")
    ctx = CapabilityContext(ResolvedProfile(slug="ноутбук"), constraints)
    cand = Candidate(source="kaspi", source_product_id="1", title="Ноутбук", price=1)
    pool = [cand]
    await bare.enrich(ctx, pool)

    assert bare.setting(ctx.profile, constraints) is None
    assert bare.query(ctx, constraints) is constraints
    assert (bare.searches(ctx), bare.relevance(ctx, pool)) == ([], set())
    assert bare.reject_listing(ctx, "Ноутбук") is None
    assert bare.hard_features(ctx, ["16 ГБ"]) == ["16 ГБ"]
    assert bare.reject_content(ctx, cand) is None
    assert bare.prepare(ctx, pool) is None and bare.axis(ctx, None, cand, {}) is None
    assert bare.notes(ctx, None, cand, ["a"], ["b"]) == (["a"], ["b"])
    assert bare.clarify(ctx) is None
    assert bare.section(ctx, Recommendation.model_construct()) is None
