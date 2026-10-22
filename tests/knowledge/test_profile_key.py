"""The profile key is the product type; old long slugs move under it (phase 2, decision 11).

No network/LLM: the store is the suite's tmp SQLite (tests/conftest.py), emptied per test.
"""

import asyncio

from buysmarter.db import repo
from buysmarter.db.base import async_session
from buysmarter.knowledge import store as store_mod
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.ingredients import ActiveSubstance, IngredientBook
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.knowledge.store import load_knowledge, resolve_profile, save_knowledge

_NIKE = {"brand_book": {"tiers": {"Nike": "A"}}}


async def _put_raw(slug: str, payload: dict) -> None:
    async with async_session() as session:
        await repo.upsert_category_knowledge(session, slug, payload)


async def _slugs() -> set[str]:
    async with async_session() as session:
        return set(await repo.list_category_slugs(session))


async def test_an_old_slug_with_audience_and_size_moves_under_the_product_type(caplog) -> None:
    await _put_raw("мужские-кроссовки-42", _NIKE)

    with caplog.at_level("INFO"):
        resolved = await resolve_profile(
            "кроссовки", search_phrase="мужские кроссовки 42", use_llm=False
        )

    assert resolved.brand_book is not None and resolved.brand_book.tiers == {"Nike": "A"}
    assert resolved.brand_book_owner == "кроссовки"
    assert await _slugs() == {"кроссовки"}
    stored = await load_knowledge("кроссовки")
    assert stored is not None and stored["synonyms"] == ["мужские-кроссовки-42"]
    assert "knowledge renamed" in caplog.text


async def test_a_narrower_profile_is_used_but_not_moved() -> None:
    """«шампунь от перхоти» may carry its own composition: it never merges into «шампунь»."""
    await _put_raw("шампунь-от-перхоти", _NIKE)

    resolved = await resolve_profile("шампунь", search_phrase="шампунь от перхоти", use_llm=False)

    assert await _slugs() == {"шампунь-от-перхоти"}
    assert resolved.brand_book_owner == "шампунь-от-перхоти"


async def test_a_found_profile_lists_an_audience_variant_as_a_synonym() -> None:
    await _put_raw("кроссовки", _NIKE)
    await _put_raw("женские-кроссовки", {"brand_book": {"tiers": {"Puma": "B"}}})

    resolved = await resolve_profile("кроссовки", search_phrase="женские кроссовки", use_llm=False)

    assert resolved.brand_book is not None and resolved.brand_book.tiers == {"Nike": "A"}
    assert await _slugs() == {"кроссовки", "женские-кроссовки"}  # books are not merged
    stored = await load_knowledge("кроссовки")
    assert stored is not None and stored["synonyms"] == ["женские-кроссовки"]
    await resolve_profile("кроссовки", search_phrase="женские кроссовки", use_llm=False)
    assert (await load_knowledge("кроссовки") or {})["synonyms"] == ["женские-кроссовки"]


async def test_children_follow_a_moved_parent() -> None:
    await _put_raw("мужские-кроссовки", _NIKE)
    await _put_raw("беговые-кроссовки", {"parent": "мужские-кроссовки"})

    await resolve_profile("кроссовки", search_phrase="мужские кроссовки", use_llm=False)

    child = await load_knowledge("беговые-кроссовки")
    assert child is not None and child["parent"] == "кроссовки"


async def test_two_lookups_at_once_move_the_record_once() -> None:
    await _put_raw("мужские-кроссовки", _NIKE)

    first, second = await asyncio.gather(
        resolve_profile("кроссовки", search_phrase="мужские кроссовки", use_llm=False),
        resolve_profile("кроссовки", search_phrase="мужские кроссовки", use_llm=False),
    )

    assert await _slugs() == {"кроссовки"}
    assert first.brand_book == second.brand_book is not None
    assert (await load_knowledge("кроссовки") or {})["synonyms"] == ["мужские-кроссовки"]


async def test_no_move_without_a_distinct_product_type(monkeypatch) -> None:
    await _put_raw("мужские-кроссовки", _NIKE)

    async def _no_rename(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("renamed")

    monkeypatch.setattr(store_mod.repo, "rename_category_knowledge", _no_rename)

    same = await resolve_profile(
        "мужские кроссовки", search_phrase="мужские кроссовки", use_llm=False
    )
    bare = await resolve_profile("мужские кроссовки", use_llm=False)

    assert same.brand_book_owner == bare.brand_book_owner == "мужские-кроссовки"
    assert await _slugs() == {"мужские-кроссовки"}


async def test_the_related_match_sees_the_search_phrase(monkeypatch) -> None:
    """Live 2026-10-22: «крем с адапаленом» keyed "крем" lost composition: Jev compared bare
    "крем" with the stored profiles. The key stays the product type; the question carries
    the phrase, so the acne profile is found."""
    await save_knowledge(
        "средства от акне",
        BrandBook(tiers={"Galderma": "A"}),
        ModelBook(),
        "opus",
        category_type="composition_driven",
        ingredient_book=IngredientBook(substances=[ActiveSubstance(canonical="адапален")]),
    )
    seen: list[str] = []

    async def _jev(state, questions):
        seen.append(repr(state) + repr(questions))
        pick = "средства-от-акне" if "адапален" in seen[-1] else "none of these"
        return {"match": {"type": "choice", "choice": pick, "confidence": 0.95}}

    async def _no_llm(*args, **kwargs):  # pragma: no cover - Jev is confident
        raise AssertionError("LLM fallback ran")

    monkeypatch.setattr(store_mod, "system_one", _jev)
    monkeypatch.setattr(store_mod, "generate", _no_llm)

    resolved = await resolve_profile("крем", search_phrase="крем с адапаленом", use_llm=True)

    assert resolved.slug == "крем"
    assert resolved.ingredient_book is not None
    assert resolved.brand_book_owner == "средства-от-акне"


def test_the_llm_fallback_prompt_carries_the_phrase() -> None:
    prompt = store_mod.match_category_prompt("крем", ["средства-от-акне"], "крем с адапаленом")

    assert "крем с адапаленом" in prompt


async def _composition_record(slug: str) -> None:
    await save_knowledge(
        slug,
        BrandBook(tiers={"Galderma": "A"}),
        ModelBook(),
        "opus",
        category_type="composition_driven",
        ingredient_book=IngredientBook(substances=[ActiveSubstance(canonical="адапален")]),
    )


async def test_an_exact_narrower_record_wins_over_the_product_type_profile() -> None:
    """Review: with a «крем» record (learned from the GUI or a rename), «крем с адапаленом»
    got the generic profile and lost composition; on main the phrase found its own record."""
    await _composition_record("крем с адапаленом")
    await _put_raw("крем", {"brand_book": {"tiers": {"Nivea": "B"}}})

    resolved = await resolve_profile("крем", search_phrase="крем с адапаленом", use_llm=False)

    assert resolved.ingredient_book is not None
    assert resolved.brand_book_owner == "крем-с-адапаленом"


async def test_a_composition_record_is_never_moved_even_as_an_audience_variant() -> None:
    """Composition profiles are narrow by nature: «детский-крем» keeps its own record."""
    await _composition_record("детский крем")

    resolved = await resolve_profile("крем", search_phrase="детский крем", use_llm=False)

    assert await _slugs() == {"детский-крем"}
    assert resolved.ingredient_book is not None
    assert resolved.brand_book_owner == "детский-крем"
