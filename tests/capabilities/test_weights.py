"""The weight rule: base + capability axes and shifts, clamped at 0, normalized to 100."""

import pytest

from buysmarter.capabilities.base import Capability, overrides
from buysmarter.capabilities.weights import combine
from buysmarter.ranking.engine import COMPOSITION_WEIGHTS, DEFAULT_WEIGHTS


class _CompositionLike(Capability):
    name = "composition"
    axis_name = "ingredient"
    axis_weight = 25.0
    weight_shifts = {"brand": -15.0, "specs": -5.0, "popularity": -5.0}


class _Durability(Capability):
    name = "durability"
    axis_name = "durability"
    axis_weight = 10.0
    weight_shifts = {"popularity": -20.0}


def test_composition_numbers_reproduce_the_old_composition_table() -> None:
    assert combine(DEFAULT_WEIGHTS, [_CompositionLike()]) == COMPOSITION_WEIGHTS


def test_two_capabilities_with_shifts_clamp_at_zero_and_sum_to_100() -> None:
    weights = combine(DEFAULT_WEIGHTS, [_CompositionLike(), _Durability()])

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
