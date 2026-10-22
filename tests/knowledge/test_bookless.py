"""A record without books (Jev's capabilities, a user's choice) is no profile (decision 1)."""

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.knowledge import store as store_mod
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.ingredients import ActiveSubstance, IngredientBook
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.knowledge.profile import COMPOSITION, has_books
from buysmarter.knowledge.store import save_knowledge

_JEV_NO = {"enabled": False, "params": {}, "set_by": "jev"}


async def _put_raw(slug: str, payload: dict) -> None:
    async with async_session() as session:
        await repo.upsert_category_knowledge(session, slug, payload)


async def _cosmetics() -> None:
    await save_knowledge(
        "косметика",
        BrandBook(tiers={"CeraVe": "B"}),
        ModelBook(),
        "opus",
        category_type="composition_driven",
        ingredient_book=IngredientBook(substances=[ActiveSubstance(canonical="адапален")]),
    )


def test_has_books_needs_a_brand_or_a_model_book() -> None:
    assert has_books({"brand_book": {"tiers": {}}})
    assert has_books({"model_book": {"entries": []}})
    assert not has_books({"capabilities": {COMPOSITION: _JEV_NO}})


async def test_a_record_without_books_does_not_cut_off_a_related_profile(monkeypatch) -> None:
    await _cosmetics()
    await _put_raw("крем-от-акне", {"capabilities": {"benchmark": _JEV_NO}})
    seen: list[list[str]] = []

    async def _choice(state, questions):
        seen.append(state["stored_categories"])
        return {"match": {"choice": "косметика", "confidence": 0.95}}

    monkeypatch.setattr(store_mod, "system_one", _choice)

    resolved = await store_mod.resolve_profile("крем от акне", use_llm=True)

    assert resolved.brand_book_owner == "косметика"
    assert resolved.ingredient_book is not None
    assert "benchmark" in resolved.capabilities  # its own capabilities still lie on top
    assert seen == [["косметика"]]


async def test_a_record_without_books_is_not_a_choice_option(monkeypatch) -> None:
    await _cosmetics()
    await _put_raw("гречка", {"capabilities": {COMPOSITION: _JEV_NO}})
    seen: list[list[str]] = []

    async def _choice(state, questions):
        seen.append(state["stored_categories"])
        return {"match": {"choice": "none of these", "confidence": 0.95}}

    monkeypatch.setattr(store_mod, "system_one", _choice)

    await store_mod.resolve_profile("шампунь", use_llm=True)

    assert seen == [["косметика"]]


async def test_a_user_choice_on_a_record_without_books_wins_over_the_related_profile(
    monkeypatch,
) -> None:
    await _cosmetics()
    await _put_raw(
        "крем-от-акне",
        {"capabilities": {COMPOSITION: {"enabled": False, "set_by": None, "user": False}}},
    )

    async def _choice(state, questions):
        return {"match": {"choice": "косметика", "confidence": 0.95}}

    monkeypatch.setattr(store_mod, "system_one", _choice)

    resolved = await store_mod.resolve_profile("крем от акне", use_llm=True)

    assert resolved.ingredient_book is None
    assert resolved.capabilities[COMPOSITION].params  # the learned book is kept, only off


async def test_jev_capabilities_never_outrank_the_related_profiles_learned_ones(
    monkeypatch,
) -> None:
    await _cosmetics()
    await _put_raw("крем-от-акне", {"capabilities": {COMPOSITION: _JEV_NO}})

    async def _choice(state, questions):
        return {"match": {"choice": "косметика", "confidence": 0.95}}

    monkeypatch.setattr(store_mod, "system_one", _choice)

    resolved = await store_mod.resolve_profile("крем от акне", use_llm=True)

    assert resolved.ingredient_book is not None


async def test_a_record_without_books_but_with_a_parent_is_the_querys_profile() -> None:
    await _cosmetics()
    await _put_raw(
        "крем-от-акне",
        {
            "capabilities": {COMPOSITION: {"enabled": False, "set_by": None, "user": False}},
            "parent": "косметика",
        },
    )

    resolved = await store_mod.resolve_profile("крем от акне", use_llm=False)

    assert resolved.found == "крем-от-акне"
    assert resolved.brand_book_owner == "косметика"
    assert resolved.ingredient_book is None


async def test_a_record_without_books_is_never_healed(monkeypatch) -> None:
    await _cosmetics()
    await _put_raw("крем-от-акне", {"capabilities": {COMPOSITION: _JEV_NO}})

    async def _choice(state, questions):
        return {"match": {"choice": "none of these", "confidence": 0.95}}

    monkeypatch.setattr(store_mod, "system_one", _choice)

    await store_mod.resolve_profile("крем от акне", use_llm=True)

    raw = await store_mod.load_knowledge("крем-от-акне") or {}
    assert "heal_checked" not in raw and "parent" not in raw


async def test_healing_never_offers_a_record_without_books(monkeypatch) -> None:
    await _cosmetics()
    await _put_raw("гречка", {"capabilities": {COMPOSITION: _JEV_NO}})
    brand = BrandBook(tiers={"La Roche-Posay": "A"})
    await _put_raw("крем-для-рук", {"brand_book": brand.model_dump(mode="json")})
    seen: list[list[str]] = []

    async def _choice(state, questions):
        seen.append(state["stored_categories"])
        return {"match": {"choice": "none of these", "confidence": 0.95}}

    monkeypatch.setattr(store_mod, "system_one", _choice)

    await store_mod.resolve_profile("крем для рук", use_llm=True)

    assert seen == [["косметика"]]
