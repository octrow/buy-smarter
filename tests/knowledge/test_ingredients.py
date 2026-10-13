"""IngredientBook resolution/merge + category_type/ingredient store roundtrip.

No network/DB/LLM: ``$BUYSMARTER_KNOWLEDGE_DIR`` points the store at a tmp path
and Postgres is unreachable, so the DB-primary path falls back to the file.
"""

import pytest

from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.ingredients import (
    ActiveSubstance,
    EquivalentProduct,
    IngredientBook,
    merge_ingredient_books,
    normalize_concentration,
)
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.knowledge.store import resolve_composition, save_knowledge


def _no_db(*args, **kwargs):
    raise RuntimeError("DB disabled in test — use file fallback")


@pytest.fixture(autouse=True)
def _tmp_knowledge_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BUYSMARTER_KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setattr("buysmarter.knowledge.store.async_session", _no_db)


def _adapalene_book() -> IngredientBook:
    return IngredientBook(
        substances=[
            ActiveSubstance(
                canonical="адапален",
                aliases=["adapalene", "Дифферин"],
                typical_concentrations=["0.1%", "0.3%"],
                equivalents=[EquivalentProduct(name="Клензит", concentration="0.1%")],
            )
        ]
    )


def test_normalize_concentration_variants() -> None:
    assert normalize_concentration("0,1 %") == "0.1%"
    assert normalize_concentration("0.1%") == "0.1%"
    assert normalize_concentration(None) is None


def test_resolve_in_text_matches_declensions() -> None:
    book = _adapalene_book()
    assert book.resolve_in_text("крем с адапаленом от акне") == ["адапален"]
    assert book.resolve_in_text("gel with adapalene") == ["адапален"]
    assert book.resolve_in_text("увлажняющий крем") == []


def test_canonical_for_alias_and_canonical() -> None:
    book = _adapalene_book()
    assert book.canonical_for("adapalene") == "адапален"
    assert book.canonical_for("АДАПАЛЕН") == "адапален"
    assert book.canonical_for("ретинол") is None


def test_merge_ingredient_books_overlay_wins() -> None:
    base = IngredientBook(substances=[ActiveSubstance(canonical="адапален", aliases=["a"])])
    overlay = IngredientBook(
        substances=[
            ActiveSubstance(canonical="адапален", aliases=["adapalene"]),
            ActiveSubstance(canonical="азелаиновая кислота"),
        ]
    )
    merged = merge_ingredient_books(base, overlay)
    by_canon = {s.canonical: s for s in merged.substances}
    assert set(by_canon) == {"адапален", "азелаиновая кислота"}
    assert by_canon["адапален"].aliases == ["adapalene"]  # overlay won


async def test_category_type_and_ingredient_roundtrip_via_file() -> None:
    await save_knowledge(
        "крем от акне",
        BrandBook(),
        ModelBook(),
        "opus",
        category_type="composition_driven",
        ingredient_book=_adapalene_book(),
    )
    category_type, book = await resolve_composition("крем от акне", use_llm=False)
    assert category_type == "composition_driven"
    assert book is not None
    assert book.canonical_for("Дифферин") == "адапален"


async def test_default_category_type_is_spec_driven() -> None:
    await save_knowledge("робот-пылесос", BrandBook(), ModelBook(), "opus")
    category_type, book = await resolve_composition("робот-пылесос", use_llm=False)
    assert category_type == "spec_driven"
    assert book is None


async def test_alias_learning_save_preserves_ingredient_book() -> None:
    """A later model-only save (learned aliases) must not wipe stored ingredients."""
    await save_knowledge(
        "крем от акне",
        BrandBook(),
        ModelBook(),
        "opus",
        category_type="composition_driven",
        ingredient_book=_adapalene_book(),
    )
    # Simulate the alias-learning save path: no category_type / ingredient passed.
    await save_knowledge("крем от акне", BrandBook(), ModelBook(), "haiku")
    category_type, book = await resolve_composition("крем от акне", use_llm=False)
    assert category_type == "composition_driven"
    assert book is not None and book.substances[0].canonical == "адапален"
