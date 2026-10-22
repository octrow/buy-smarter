"""CLI tests for `learn-category` / `learn` with stubbed LLM generation.

No network/LLM: the generation/extraction coroutines are monkeypatched; the store is the
suite's tmp SQLite (tests/conftest.py).
"""

import pytest
from typer.testing import CliRunner

from buysmarter.cli import main
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.ingredients import (
    ActiveSubstance,
    EquivalentProduct,
    IngredientBook,
)
from buysmarter.knowledge.models_book import ModelBook, ModelEntry
from buysmarter.knowledge.store import get_stored_books, resolve_composition

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_price_research(monkeypatch) -> None:
    """The official-store check fetches live pages: off in these tests."""

    async def _same(book: BrandBook, category: str) -> BrandBook:
        return book

    monkeypatch.setattr(main, "verify_store_floors", _same)


def _stub_books() -> tuple[BrandBook, ModelBook]:
    brand = BrandBook(
        tiers={"Roborock": "A", "Dreame": "B", "Xiaomi": "C"},
        aliases={"Сяоми": "Xiaomi"},
    )
    model = ModelBook(entries=[ModelEntry(canonical="Roborock S8", brand="Roborock", codes=["S8"])])
    return brand, model


def test_learn_category_generates_saves_and_summarizes(monkeypatch) -> None:
    async def _fake_generate(category: str, *, web_search: bool = True):
        brand, model = _stub_books()
        return brand, model, "spec_driven", IngredientBook()

    monkeypatch.setattr(main, "generate_category_knowledge", _fake_generate)

    result = runner.invoke(main.app, ["learn-category", "робот-пылесос"])
    assert result.exit_code == 0, result.output
    assert "Saved knowledge" in result.output
    assert "web search" in result.output  # web grounding on by default
    assert "Roborock" in result.output
    assert "Models: 1" in result.output
    assert "spec_driven" in result.output

    # Persisted and reloadable from the store.
    import asyncio

    brand, model = asyncio.run(get_stored_books("робот-пылесос"))
    assert brand is not None and brand.tiers["Roborock"] == "A"
    assert model.entries[0].canonical == "Roborock S8"


def test_learn_category_no_web_flag_disables_web_search(monkeypatch) -> None:
    seen: dict = {}

    async def _fake_generate(category: str, *, web_search: bool = True):
        seen["web_search"] = web_search
        brand, model = _stub_books()
        return brand, model, "spec_driven", IngredientBook()

    monkeypatch.setattr(main, "generate_category_knowledge", _fake_generate)

    result = runner.invoke(main.app, ["learn-category", "робот-пылесос", "--no-web"])

    assert result.exit_code == 0, result.output
    assert seen["web_search"] is False
    assert "web search" not in result.output


def test_learn_category_composition_driven_saves_ingredient_book(monkeypatch) -> None:
    """A composition-driven category persists its type + ingredient book (roundtrip)."""
    import asyncio

    ingredient = IngredientBook(
        substances=[
            ActiveSubstance(
                canonical="адапален",
                aliases=["adapalene", "Дифферин"],
                typical_concentrations=["0.1%", "0.3%"],
                equivalents=[
                    EquivalentProduct(name="Клензит", concentration="0.1%"),
                    EquivalentProduct(name="Differin", concentration="0.1%"),
                ],
            )
        ]
    )

    async def _fake_generate(category: str, *, web_search: bool = True):
        return BrandBook(), ModelBook(), "composition_driven", ingredient

    monkeypatch.setattr(main, "generate_category_knowledge", _fake_generate)

    result = runner.invoke(main.app, ["learn-category", "крем от акне"])
    assert result.exit_code == 0, result.output
    assert "composition_driven" in result.output
    assert "адапален" in result.output

    category_type, stored = asyncio.run(resolve_composition("крем от акне", use_llm=False))
    assert category_type == "composition_driven"
    assert stored is not None
    assert stored.canonical_for("adapalene") == "адапален"
    assert stored.substances[0].typical_concentrations == ["0.1%", "0.3%"]


def test_learn_file_merges_user_notes_over_stored(monkeypatch, tmp_path) -> None:
    # Existing stored book: Xiaomi at tier C.
    import asyncio

    from buysmarter.knowledge.store import save_knowledge

    base_brand = BrandBook(tiers={"Xiaomi": "C"}, aliases={})
    base_model = ModelBook(entries=[])
    asyncio.run(save_knowledge("робот-пылесос", base_brand, base_model, "opus"))

    async def _fake_extract(category: str, notes: str):
        # User's notes promote Xiaomi to B and add Roborock.
        return (
            BrandBook(tiers={"Xiaomi": "B", "Roborock": "A"}, aliases={}),
            ModelBook(entries=[]),
            "spec_driven",
            IngredientBook(),
        )

    monkeypatch.setattr(main, "extract_category_knowledge", _fake_extract)

    notes = tmp_path / "notes.md"
    notes.write_text("# my research\nXiaomi is actually solid", encoding="utf-8")

    result = runner.invoke(main.app, ["learn", str(notes), "--category", "робот-пылесос"])
    assert result.exit_code == 0, result.output

    brand, _ = asyncio.run(get_stored_books("робот-пылесос"))
    assert brand.tiers == {"Xiaomi": "B", "Roborock": "A"}  # user file won


def test_learn_file_merges_with_the_own_record_not_a_parent(monkeypatch, tmp_path) -> None:
    """Decision 6: the parent's books and capabilities are not copied into the child."""
    import asyncio

    from buysmarter.knowledge.store import load_knowledge, save_knowledge, update_knowledge

    asyncio.run(
        save_knowledge(
            "косметика",
            BrandBook(tiers={"CeraVe": "B"}),
            ModelBook(),
            "opus",
            category_type="composition_driven",
            ingredient_book=IngredientBook(substances=[ActiveSubstance(canonical="адапален")]),
        )
    )
    asyncio.run(update_knowledge("крем-от-акне", {"parent": "косметика"}, "user"))

    async def _fake_extract(category: str, notes: str):
        return BrandBook(tiers={"Galderma": "A"}), ModelBook(), "spec_driven", IngredientBook()

    monkeypatch.setattr(main, "extract_category_knowledge", _fake_extract)
    notes = tmp_path / "notes.md"
    notes.write_text("Galderma", encoding="utf-8")

    result = runner.invoke(main.app, ["learn", str(notes), "--category", "крем от акне"])

    assert result.exit_code == 0, result.output
    raw = asyncio.run(load_knowledge("крем от акне")) or {}
    assert raw["brand_book"]["tiers"] == {"Galderma": "A"}  # no CeraVe from the parent
    assert raw["parent"] == "косметика"
    assert "capabilities" not in raw and "category_type" not in raw  # still inherits


def test_learn_file_fails_and_saves_nothing_on_truncated_json(monkeypatch, tmp_path) -> None:
    import asyncio

    from buysmarter.llm.adapter import LlmError

    async def _truncated(category: str, notes: str):
        raise LlmError("claude CLI response invalid after retry: Expecting ',' delimiter")

    monkeypatch.setattr(main, "extract_category_knowledge", _truncated)
    notes = tmp_path / "notes.md"
    notes.write_text("- Samsung Galaxy S24", encoding="utf-8")

    result = runner.invoke(main.app, ["learn", str(notes), "--category", "смартфон"])

    assert result.exit_code == 1
    assert "Learning from file failed" in result.output
    assert "split the notes" in result.output
    assert asyncio.run(get_stored_books("смартфон")) == (None, None)
