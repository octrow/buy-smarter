"""Store roundtrip, merge logic, and resolution order.

No network/LLM: the store is the suite's tmp SQLite (tests/conftest.py), emptied per test.
"""

from buysmarter.knowledge import store as store_mod
from buysmarter.knowledge.brands import IRRIGATOR_BRAND_BOOK, BrandBook
from buysmarter.knowledge.models_book import IRRIGATOR_MODEL_BOOK, ModelBook, ModelEntry
from buysmarter.knowledge.store import (
    get_stored_books,
    merge_brand_books,
    merge_model_books,
    resolve_books,
    save_knowledge,
)


def _sample_books() -> tuple[BrandBook, ModelBook]:
    brand = BrandBook(tiers={"Roborock": "A", "Xiaomi": "C"}, aliases={"Сяоми": "Xiaomi"})
    model = ModelBook(entries=[ModelEntry(canonical="Roborock S8", brand="Roborock", codes=["S8"])])
    return brand, model


async def test_save_then_load_roundtrip() -> None:
    brand, model = _sample_books()
    await save_knowledge("робот-пылесос", brand, model, "opus")

    got_brand, got_model = await get_stored_books("робот-пылесос")
    assert got_brand is not None and got_model is not None
    assert got_brand.tiers == {"Roborock": "A", "Xiaomi": "C"}
    assert got_brand.aliases == {"Сяоми": "Xiaomi"}
    assert got_model.entries[0].canonical == "Roborock S8"


async def test_get_stored_books_missing_category_is_none() -> None:
    assert await get_stored_books("unknown-category") == (None, None)


async def test_resolution_prefers_stored_over_seed() -> None:
    # "ирригатор" has a static seed; a stored book must win over it.
    brand = BrandBook(tiers={"Waterpik": "A"}, aliases={})
    model = ModelBook(entries=[ModelEntry(canonical="Custom X", brand="Waterpik")])
    await save_knowledge("ирригатор", brand, model, "opus")

    got_brand, got_model = await resolve_books("ирригатор")
    assert got_brand is not brand  # re-parsed, not the same instance
    assert got_brand.tiers == {"Waterpik": "A"}
    assert got_brand is not IRRIGATOR_BRAND_BOOK
    assert got_model.entries[0].canonical == "Custom X"


async def test_resolution_falls_back_to_seed_when_unstored() -> None:
    got_brand, got_model = await resolve_books("ирригатор портативный")
    assert got_brand is IRRIGATOR_BRAND_BOOK
    assert got_model is IRRIGATOR_MODEL_BOOK


async def test_resolution_none_when_no_store_and_no_seed() -> None:
    assert await resolve_books("совершенно новая категория") == (None, None)


# --- Related-category matching (fix B: "sunscreen" must find "kosmetika") -


async def test_resolution_falls_back_to_substring_match_no_llm(caplog) -> None:
    """Pure fallback: a stored "косметика" book covers a narrower "крем" query."""
    brand = BrandBook(tiers={"La Roche-Posay": "A", "CeraVe": "B"}, aliases={})
    model = ModelBook(entries=[])
    await save_knowledge("косметика", brand, model, "opus")

    with caplog.at_level("INFO"):
        got_brand, got_model = await resolve_books("солнцезащитный крем-косметика", use_llm=False)

    assert got_brand is not None
    assert got_brand.tiers == {"La Roche-Posay": "A", "CeraVe": "B"}
    assert "knowledge matched" in caplog.text


async def test_resolution_matches_via_llm_when_substring_misses(monkeypatch) -> None:
    """LLM path: "sunscreen" doesn't substring-match "kosmetika" but should."""
    brand = BrandBook(tiers={"La Roche-Posay": "A"}, aliases={})
    model = ModelBook(entries=[])
    await save_knowledge("косметика", brand, model, "opus")

    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return response_model(index=0)

    monkeypatch.setattr(store_mod, "generate", _fake_generate)

    got_brand, got_model = await resolve_books("sunscreen", use_llm=True)

    assert got_brand is not None
    assert got_brand.tiers == {"La Roche-Posay": "A"}


async def test_resolution_llm_null_index_returns_seed_fallback(monkeypatch) -> None:
    """LLM says no stored category applies -> falls through to seed/None, not a crash."""
    brand = BrandBook(tiers={"La Roche-Posay": "A"}, aliases={})
    model = ModelBook(entries=[])
    await save_knowledge("косметика", brand, model, "opus")

    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return response_model(index=None)

    monkeypatch.setattr(store_mod, "generate", _fake_generate)

    assert await resolve_books("sunscreen", use_llm=True) == (None, None)


async def test_resolution_llm_failure_is_graceful(monkeypatch) -> None:
    """An LLM error during category matching must not raise — fail to None."""
    brand = BrandBook(tiers={"La Roche-Posay": "A"}, aliases={})
    model = ModelBook(entries=[])
    await save_knowledge("косметика", brand, model, "opus")

    async def _boom(prompt, response_model, *, model=None, **kwargs):
        raise store_mod.LlmError("cli down")

    monkeypatch.setattr(store_mod, "generate", _boom)

    assert await resolve_books("sunscreen", use_llm=True) == (None, None)


# --- Jev first, LLM fallback ----------------------------------------------


def _jev_choice(choice: str, confidence: float):
    async def _fake_system_one(state, questions):
        return {"match": {"type": "choice", "choice": choice, "confidence": confidence}}

    return _fake_system_one


def _llm_must_not_run(*args, **kwargs):
    raise AssertionError("LLM fallback ran although Jev was confident")


async def test_jev_confident_match_skips_llm(monkeypatch) -> None:
    await save_knowledge("косметика", BrandBook(tiers={"CeraVe": "B"}), ModelBook(), "opus")
    monkeypatch.setattr(store_mod, "system_one", _jev_choice("косметика", 0.98))
    monkeypatch.setattr(store_mod, "generate", _llm_must_not_run)

    got_brand, _ = await resolve_books("sunscreen", use_llm=True)

    assert got_brand is not None
    assert got_brand.tiers == {"CeraVe": "B"}


async def test_jev_confident_no_match_returns_none(monkeypatch) -> None:
    await save_knowledge("косметика", BrandBook(tiers={"CeraVe": "B"}), ModelBook(), "opus")
    monkeypatch.setattr(store_mod, "system_one", _jev_choice(store_mod._NO_MATCH, 1.0))
    monkeypatch.setattr(store_mod, "generate", _llm_must_not_run)

    assert await resolve_books("ноутбук", use_llm=True) == (None, None)


async def test_jev_unsure_escalates_to_llm(monkeypatch) -> None:
    await save_knowledge("косметика", BrandBook(tiers={"CeraVe": "B"}), ModelBook(), "opus")
    monkeypatch.setattr(store_mod, "system_one", _jev_choice("косметика", 0.51))
    calls: list[str] = []

    async def _llm(prompt, response_model, *, model=None, **kwargs):
        calls.append(model)
        return response_model(index=None)

    monkeypatch.setattr(store_mod, "generate", _llm)

    assert await resolve_books("зубная щетка", use_llm=True) == (None, None)
    assert calls == ["free-stack"]  # the LLM's "no match" wins over Jev's unsure pick


def test_jev_question_lists_stored_slugs_and_no_match() -> None:
    question = store_mod.match_category_question("sunscreen", ["косметика", "смартфон"])
    assert question["type"] == "choice"
    assert list(question["criteria"]) == ["косметика", "смартфон", store_mod._NO_MATCH]


def test_merge_brand_books_overlay_wins_on_conflict() -> None:
    base = BrandBook(tiers={"Xiaomi": "C", "Dreame": "B"}, aliases={"Сяоми": "Xiaomi"})
    overlay = BrandBook(tiers={"Xiaomi": "B", "Roborock": "A"}, aliases={"Робо": "Roborock"})

    merged = merge_brand_books(base, overlay)
    assert merged.tiers == {"Xiaomi": "B", "Dreame": "B", "Roborock": "A"}
    assert merged.aliases == {"Сяоми": "Xiaomi", "Робо": "Roborock"}


def test_merge_model_books_overlay_wins_by_canonical() -> None:
    base = ModelBook(
        entries=[
            ModelEntry(canonical="Roborock S8", brand="Roborock", codes=["S8"]),
            ModelEntry(canonical="Xiaomi X10", brand="Xiaomi"),
        ]
    )
    overlay = ModelBook(
        entries=[ModelEntry(canonical="Roborock S8", brand="Roborock", codes=["S8", "S8PRO"])]
    )

    merged = merge_model_books(base, overlay)
    by_canon = {e.canonical: e for e in merged.entries}
    assert set(by_canon) == {"Roborock S8", "Xiaomi X10"}
    assert by_canon["Roborock S8"].codes == ["S8", "S8PRO"]


# --- one-time import of the pre-store JSON files ------------------------------


def _legacy_dir():
    import os
    import shutil
    from pathlib import Path

    path = Path(os.environ["BUYSMARTER_KNOWLEDGE_DIR"])
    for leftover in (path, path.with_name(path.name + ".imported")):
        shutil.rmtree(leftover, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _legacy_payload(canonical: str) -> str:
    import json

    model = ModelBook(entries=[ModelEntry(canonical=canonical, brand="Acme", codes=["X1"])])
    return json.dumps(
        {"brand_book": BrandBook().model_dump(mode="json"), "model_book": model.model_dump()},
        ensure_ascii=False,
    )


async def test_legacy_import_adds_missing_categories_and_keeps_stored_ones() -> None:
    legacy = _legacy_dir()
    brand, model = _sample_books()
    await save_knowledge("пылесос", brand, model, generated_by="test")
    (legacy / "пылесос.json").write_text(_legacy_payload("Old"), encoding="utf-8")
    (legacy / "фен.json").write_text(_legacy_payload("Acme X1"), encoding="utf-8")
    (legacy / "битый.json").write_text("{not json", encoding="utf-8")

    await store_mod.import_legacy_dir()

    _, kept = await get_stored_books("пылесос")
    _, imported = await get_stored_books("фен")
    assert kept is not None and [e.canonical for e in kept.entries] == ["Roborock S8"]
    assert imported is not None and [e.canonical for e in imported.entries] == ["Acme X1"]
    assert await get_stored_books("битый") == (None, None)
    assert not legacy.exists() and legacy.with_name(legacy.name + ".imported").is_dir()
