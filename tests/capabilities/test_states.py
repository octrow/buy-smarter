"""What each capability did for a query and who decided it (phase 3, decisions 13 and 14)."""

import pytest

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.provenance import capability_states, toggle_text
from buysmarter.knowledge.ingredients import ActiveSubstance, IngredientBook
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import QueryConstraints

_BOOK = IngredientBook(substances=[ActiveSubstance(canonical="адапален")]).model_dump(mode="json")
_PHONE = QueryConstraints(search_phrase="смартфон", product_type="смартфон")
_CREAM = QueryConstraints(search_phrase="крем от акне", product_type="крем от акне")


def _states(profile: ResolvedProfile, constraints=_CREAM):
    return {s.name: s for s in capability_states(REGISTRY, profile, constraints)}


def _profile(**settings: CapabilitySetting) -> ResolvedProfile:
    return ResolvedProfile(slug="x", capabilities=settings)


def test_a_learned_composition_with_a_book_is_on_and_active() -> None:
    learned = CapabilitySetting(params={"ingredient_book": _BOOK})
    state = _states(_profile(composition=learned))["composition"]

    assert (state.on, state.active, state.set_by, state.user) == (True, True, "learned", None)
    assert state.label == "Состав"


def test_the_users_off_switch_is_off_but_keeps_the_automatic_value() -> None:
    learned = CapabilitySetting(params={"ingredient_book": _BOOK}, user=False)
    state = _states(_profile(composition=learned))["composition"]

    assert (state.on, state.active, state.auto, state.user) == (False, False, True, False)


def test_a_user_only_entry_of_benchmark_leaves_the_rule_as_the_automatic_value() -> None:
    off = CapabilitySetting(enabled=False, set_by=None, user=False)
    state = _states(_profile(benchmark=off), _PHONE)["benchmark"]

    assert (state.on, state.auto, state.set_by, state.user) == (False, True, "fallback", False)
    assert state.label == "Бенчмарк Kimovil"


def test_benchmark_by_the_rule_alone_is_on_with_the_rule_as_source() -> None:
    state = _states(_profile(), _PHONE)["benchmark"]

    assert (state.on, state.active, state.set_by) == (True, True, "fallback")


def test_a_capability_nobody_switched_on_is_off_without_a_source() -> None:
    state = _states(_profile())["composition"]

    assert (state.on, state.active, state.set_by, state.user) == (False, False, None, None)


def test_the_levels_come_from_the_capabilities() -> None:
    levels = {s.name: s.level for s in capability_states(REGISTRY, _profile(), _CREAM)}

    assert levels == {
        "audience": "request",
        "size": "request",
        "benchmark": "category",
        "composition": "category",
    }


@pytest.mark.parametrize(
    ("setting", "text"),
    [
        (CapabilitySetting(params={"ingredient_book": _BOOK}), "Состав — включено обучением"),
        (
            CapabilitySetting(params={"ingredient_book": _BOOK}, user=False),
            "Состав — выключено вами",
        ),
        (
            CapabilitySetting(params={"ingredient_book": _BOOK}, user=True),
            "Состав — включено вами",
        ),
        (
            CapabilitySetting(params={"ingredient_book": _BOOK}, set_by="jev"),
            "Состав — включено Jev",
        ),
        (CapabilitySetting(enabled=False), "Состав — выключено обучением"),
        (CapabilitySetting(set_by="jev"), "Состав — включено Jev, но нет данных"),
    ],
)
def test_the_toggle_text_names_the_state_and_who_set_it(setting, text) -> None:
    state = _states(_profile(composition=setting))["composition"]

    assert toggle_text(state) == text


def test_the_toggle_text_of_an_untouched_capability_says_so() -> None:
    assert toggle_text(_states(_profile())["composition"]) == "Состав — не включено"
