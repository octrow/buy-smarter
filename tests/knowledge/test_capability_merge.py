"""One merge point for capabilities: by name, by precedence, the user's choice untouched.

Every writer of the ``capabilities`` key goes through ``update_knowledge`` (phase 3,
decision 3); none of them may change ``user``, and a reset keeps the learned parameters.
"""

from pathlib import Path

from typer.testing import CliRunner

from buysmarter.cli import main
from buysmarter.knowledge import generate as generate_module
from buysmarter.knowledge import store as store_mod
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.ingredients import ActiveSubstance, IngredientBook
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.knowledge.profile import COMPOSITION, CapabilitySetting, merge_capabilities
from buysmarter.knowledge.store import (
    load_knowledge,
    save_knowledge,
    set_user_choice,
    update_knowledge,
)

runner = CliRunner()
_BOOK = IngredientBook(substances=[ActiveSubstance(canonical="адапален")])


def _jev(enabled: bool) -> dict:
    return {"enabled": enabled, "params": {}, "set_by": "jev"}


def test_a_weaker_source_never_overwrites_a_stronger_one() -> None:
    stored = {COMPOSITION: CapabilitySetting(params={"ingredient_book": {"substances": []}})}

    merged = merge_capabilities(stored, {COMPOSITION: _jev(False)}, by_user=False)

    assert merged[COMPOSITION]["set_by"] == "learned" and merged[COMPOSITION]["enabled"]


def test_an_automatic_writer_never_sets_the_users_choice() -> None:
    stored = {COMPOSITION: CapabilitySetting(set_by="jev", user=False)}
    incoming = {COMPOSITION: {"enabled": True, "set_by": "learned", "user": True}}

    merged = merge_capabilities(stored, incoming, by_user=False)

    assert merged[COMPOSITION]["user"] is False
    assert merged[COMPOSITION]["set_by"] == "learned"


def test_the_users_writer_changes_only_the_choice_and_a_bare_reset_removes_the_entry() -> None:
    stored = {COMPOSITION: CapabilitySetting(set_by="jev", enabled=True)}

    chosen = merge_capabilities(stored, {"benchmark": {"user": True}}, by_user=True)
    assert chosen["benchmark"] == {"enabled": False, "params": {}, "set_by": None, "user": True}
    assert chosen[COMPOSITION]["set_by"] == "jev"

    back = {n: CapabilitySetting.model_validate(s) for n, s in chosen.items()}
    assert "benchmark" not in merge_capabilities(back, {"benchmark": {"user": None}}, by_user=True)


async def test_reset_keeps_the_learned_parameters() -> None:
    await save_knowledge("косметика", category_type="composition_driven", ingredient_book=_BOOK)
    await set_user_choice("косметика", COMPOSITION, False)
    profile = await store_mod.load_profile("косметика")
    assert profile is not None and profile.ingredient_book is None

    await set_user_choice("косметика", COMPOSITION, None)

    profile = await store_mod.load_profile("косметика")
    assert profile is not None and profile.ingredient_book == _BOOK


async def test_a_choice_on_a_related_profile_writes_the_querys_record_with_a_parent() -> None:
    await save_knowledge("косметика", BrandBook(), ModelBook(), "opus")

    await set_user_choice("крем-от-акне", COMPOSITION, False, parent="косметика")

    raw = await load_knowledge("крем-от-акне") or {}
    assert raw["parent"] == "косметика"
    assert raw["capabilities"][COMPOSITION]["user"] is False
    assert "capabilities" not in (await load_knowledge("косметика") or {})


async def _user_off(slug: str) -> None:
    await save_knowledge(slug, BrandBook(), ModelBook(), "opus")
    await set_user_choice(slug, COMPOSITION, False)


async def _user_choice(slug: str) -> bool | None:
    raw = await load_knowledge(slug) or {}
    return CapabilitySetting.model_validate(raw["capabilities"][COMPOSITION]).user


async def test_learning_keeps_the_users_choice(monkeypatch) -> None:
    await _user_off("крем-от-акне")

    async def _knowledge(category: str, *, web_search: bool = True):
        return BrandBook(), ModelBook(), "composition_driven", _BOOK

    async def _same(book: BrandBook, category: str) -> BrandBook:
        return book

    monkeypatch.setattr(generate_module, "generate_category_knowledge", _knowledge)
    monkeypatch.setattr(generate_module, "verify_store_floors", _same)

    await generate_module.learn_category("крем-от-акне")

    assert await _user_choice("крем-от-акне") is False
    raw = await load_knowledge("крем-от-акне") or {}
    assert raw["capabilities"][COMPOSITION]["params"]["ingredient_book"]  # learned, kept off
    assert raw["category_type"] == "spec_driven"


def test_the_cli_learn_category_goes_through_learn_category(monkeypatch) -> None:
    seen: list[str] = []

    async def _learn(category: str, *, web_search: bool = True):
        seen.append(category)
        return BrandBook(), ModelBook(), "spec_driven", IngredientBook()

    monkeypatch.setattr(main, "learn_category_knowledge", _learn)

    result = runner.invoke(main.app, ["learn-category", "робот-пылесос"])

    assert result.exit_code == 0, result.output
    assert seen == ["робот-пылесос"]


async def test_learning_from_notes_keeps_the_users_choice(monkeypatch, tmp_path: Path) -> None:
    await _user_off("крем-от-акне")

    async def _extract(category: str, notes: str):
        return BrandBook(), ModelBook(), "composition_driven", _BOOK

    monkeypatch.setattr(main, "extract_category_knowledge", _extract)
    notes = tmp_path / "notes.md"
    notes.write_text("адапален", encoding="utf-8")

    await main._run_learn_file(notes, "крем-от-акне")

    assert await _user_choice("крем-от-акне") is False


async def test_jev_writes_keep_the_users_choice() -> None:
    await _user_off("гречка")

    await update_knowledge("гречка", {"capabilities": {COMPOSITION: _jev(True)}}, "jev")

    assert await _user_choice("гречка") is False


async def test_synonyms_and_healing_writes_keep_the_users_choice() -> None:
    await _user_off("косметика")

    await update_knowledge("косметика", {"synonyms": ["cosmetics"]}, "knowledge-rename")
    await save_knowledge("косметика", model_book=ModelBook(), generated_by="knowledge-heal")

    assert await _user_choice("косметика") is False
