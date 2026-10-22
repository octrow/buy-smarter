"""Long notes are extracted in chunks whose results are merged; short notes stay one call."""

import asyncio

import pytest

from buysmarter.knowledge import generate as generate_module
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.generate import (
    MAX_MODELS_PER_CHUNK,
    CategoryKnowledgeResult,
    extract_category_knowledge,
    extract_prompt,
)
from buysmarter.knowledge.ingredients import ActiveSubstance, IngredientBook
from buysmarter.knowledge.models_book import ModelBook, ModelEntry
from buysmarter.llm.adapter import LlmError

PREAMBLE = "# Phones\nBrand: Samsung (tier A), Xiaomi (tier C)"


def _notes(n_models: int) -> str:
    items = "\n".join(f"- Phone {i}\n  - 8/256 GB" for i in range(n_models))
    return f"{PREAMBLE}\n\n## Models\n{items}\n"


def _patch(monkeypatch, *, fail_on: str | None = None) -> list[str]:
    prompts: list[str] = []

    async def _fake_generate(prompt, response_model, **kwargs):
        prompts.append(prompt)
        if fail_on and fail_on in prompt:
            raise LlmError("cut off")
        names = [ln[2:] for ln in prompt.splitlines() if ln.startswith("- Phone ")]
        composition = "- Phone 0\n" in prompt
        return CategoryKnowledgeResult(
            brand_book=BrandBook(tiers={f"B{len(prompts)}": "A"}),
            model_book=ModelBook(entries=[ModelEntry(canonical=n, brand="X") for n in names]),
            category_type="composition_driven" if composition else "spec_driven",
            ingredient_book=IngredientBook(
                substances=[ActiveSubstance(canonical="адапален")] if composition else []
            ),
        )

    monkeypatch.setattr(generate_module, "generate", _fake_generate)
    return prompts


def test_short_notes_make_one_call_with_unchanged_prompt(monkeypatch) -> None:
    prompts = _patch(monkeypatch)
    notes = _notes(MAX_MODELS_PER_CHUNK)

    asyncio.run(extract_category_knowledge("смартфон", notes))

    assert prompts == [extract_prompt("смартфон", notes)]


def test_long_notes_are_split_and_merged(monkeypatch) -> None:
    prompts = _patch(monkeypatch)
    total = MAX_MODELS_PER_CHUNK * 2 + 5

    knowledge = asyncio.run(extract_category_knowledge("смартфон", _notes(total)))

    assert len(prompts) == 3
    entries = knowledge.model.entries
    assert sorted(e.canonical for e in entries) == sorted(f"Phone {i}" for i in range(total))
    assert set(knowledge.brand.tiers) == {"B1", "B2", "B3"}
    assert knowledge.category_type == "composition_driven"  # only the first chunk said so
    assert [s.canonical for s in knowledge.ingredient.substances] == ["адапален"]


def test_preamble_and_section_heading_reach_every_chunk(monkeypatch) -> None:
    prompts = _patch(monkeypatch)

    asyncio.run(extract_category_knowledge("смартфон", _notes(MAX_MODELS_PER_CHUNK * 3)))

    assert len(prompts) == 3
    for prompt in prompts:
        assert "Brand: Samsung (tier A), Xiaomi (tier C)" in prompt
        assert "## Models" in prompt


def test_one_failed_chunk_fails_the_whole_extraction(monkeypatch) -> None:
    _patch(monkeypatch, fail_on=f"- Phone {MAX_MODELS_PER_CHUNK + 1}\n")

    with pytest.raises(LlmError):
        asyncio.run(extract_category_knowledge("смартфон", _notes(MAX_MODELS_PER_CHUNK * 2)))
