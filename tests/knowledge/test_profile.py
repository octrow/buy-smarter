"""Category profile: the old-record adapter, the dual write, one-step inheritance and owners."""

from buysmarter.knowledge import store as store_mod
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.ingredients import ActiveSubstance, IngredientBook
from buysmarter.knowledge.models_book import ModelBook, ModelEntry
from buysmarter.knowledge.profile import (
    COMPOSITION,
    CapabilitySetting,
    CategoryProfile,
    profile_from_record,
    resolve_fields,
)
from buysmarter.knowledge.store import save_knowledge


def _ingredients() -> IngredientBook:
    return IngredientBook(substances=[ActiveSubstance(canonical="адапален")])


def test_an_old_composition_record_reads_as_the_composition_capability() -> None:
    raw = {
        "brand_book": BrandBook(tiers={"CeraVe": "B"}).model_dump(mode="json"),
        "category_type": "composition_driven",
        "ingredient_book": _ingredients().model_dump(mode="json"),
    }

    profile = profile_from_record("косметика", raw)

    assert profile.capabilities is not None
    setting = profile.capabilities[COMPOSITION]
    assert setting.enabled and setting.set_by == "learned"
    assert profile.ingredient_book == _ingredients()
    assert profile.brand_book is not None and profile.model_book is None


def test_an_old_spec_driven_record_sets_no_capabilities() -> None:
    profile = profile_from_record("пылесос", {"category_type": "spec_driven"})

    assert profile.capabilities is None
    assert profile.ingredient_book is None


async def test_saving_a_category_type_writes_the_capability_and_the_old_keys() -> None:
    await save_knowledge(
        "косметика",
        BrandBook(),
        ModelBook(),
        "opus",
        category_type="composition_driven",
        ingredient_book=_ingredients(),
    )

    raw = await store_mod.load_knowledge("косметика") or {}
    assert raw["category_type"] == "composition_driven"
    assert raw["ingredient_book"] == _ingredients().model_dump(mode="json")
    composition = raw["capabilities"][COMPOSITION]
    assert composition["params"]["ingredient_book"] == raw["ingredient_book"]


async def test_relearning_as_spec_driven_drops_only_the_composition_capability() -> None:
    await save_knowledge(
        "косметика", category_type="composition_driven", ingredient_book=_ingredients()
    )
    raw = await store_mod.load_knowledge("косметика") or {}
    raw["capabilities"]["benchmark"] = {"enabled": True, "params": {}, "set_by": "user"}
    await store_mod.update_knowledge("косметика", raw, "user")

    await save_knowledge("косметика", category_type="spec_driven", generated_by="opus")

    raw = await store_mod.load_knowledge("косметика") or {}
    assert set(raw["capabilities"]) == {"benchmark"}
    assert raw["category_type"] == "spec_driven"


def test_a_field_the_profile_lacks_comes_from_the_parent_with_its_owner() -> None:
    parent_book = ModelBook(entries=[ModelEntry(canonical="Клензит", brand="Glenmark")])
    parent = CategoryProfile(
        slug="косметика",
        brand_book=BrandBook(tiers={"CeraVe": "B"}),
        model_book=parent_book,
        capabilities={COMPOSITION: CapabilitySetting()},
    )
    child = CategoryProfile(
        slug="крем-от-акне", brand_book=BrandBook(tiers={"Galderma": "A"}), parent="косметика"
    )

    resolved = resolve_fields("крем-от-акне", child, parent)

    assert resolved.brand_book is not None and resolved.brand_book.tiers == {"Galderma": "A"}
    assert resolved.brand_book_owner == "крем-от-акне"
    assert resolved.model_book == parent_book and resolved.model_book_owner == "косметика"
    assert resolved.enabled(COMPOSITION) is not None
    assert resolved.capabilities_owner == "косметика"


def test_own_capabilities_win_over_the_parent_even_when_empty() -> None:
    parent = CategoryProfile(slug="косметика", capabilities={COMPOSITION: CapabilitySetting()})
    child = CategoryProfile(slug="бритва", capabilities={}, parent="косметика")

    resolved = resolve_fields("бритва", child, parent)

    assert resolved.enabled(COMPOSITION) is None
    assert resolved.capabilities_owner == "бритва"


def test_nothing_stored_resolves_to_no_owner() -> None:
    resolved = resolve_fields("ноутбук", None, None)

    assert resolved.brand_book is None and resolved.brand_book_owner is None
    assert resolved.capabilities == {}
