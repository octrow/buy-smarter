"""Tests for LLM-driven category-knowledge generation. No network/CLI/DB —
``llm.adapter.generate`` is monkeypatched to capture what would be sent."""

from buysmarter.knowledge import generate as generate_module
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.generate import (
    _WEB_RESEARCH_GUIDE,
    CategoryKnowledgeResult,
    generate_category_knowledge,
    generate_prompt,
)
from buysmarter.knowledge.ingredients import IngredientBook
from buysmarter.knowledge.models_book import ModelBook


def _result() -> CategoryKnowledgeResult:
    return CategoryKnowledgeResult(
        brand_book=BrandBook(),
        model_book=ModelBook(),
        category_type="spec_driven",
        ingredient_book=IngredientBook(),
    )


def _patch_generate(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def _fake_generate(prompt, response_model, **kwargs):
        calls.append({"prompt": prompt, "response_model": response_model, **kwargs})
        return _result()

    monkeypatch.setattr(generate_module, "generate", _fake_generate)
    return calls


# --- prompt construction -----------------------------------------------------


def test_generate_prompt_includes_research_preamble_by_default():
    prompt = generate_prompt("робот-пылесос")
    assert _WEB_RESEARCH_GUIDE in prompt
    assert "лучшие <category>" in prompt


def test_generate_prompt_omits_preamble_when_web_search_disabled():
    prompt = generate_prompt("робот-пылесос", web_search=False)
    assert _WEB_RESEARCH_GUIDE not in prompt


def test_generate_prompt_schema_guide_unchanged_by_web_search_flag():
    """The schema guide section itself must be identical either way — only
    the preamble differs."""
    with_web = generate_prompt("робот-пылесос", web_search=True)
    without_web = generate_prompt("робот-пылесос", web_search=False)
    # The schema guide text (everything after the preamble) is byte-identical.
    schema_guide = without_web.split("knowledge.\n\n", 1)[1]
    assert with_web.endswith(schema_guide)


# --- generate_category_knowledge: web_search wiring -------------------------


async def test_generate_category_knowledge_web_search_uses_websearch_tool(monkeypatch):
    calls = _patch_generate(monkeypatch)

    await generate_category_knowledge("робот-пылесос", web_search=True)

    assert len(calls) == 1
    assert _WEB_RESEARCH_GUIDE in calls[0]["prompt"]
    assert calls[0]["cli_tools"] == "WebSearch"
    assert calls[0]["cli_timeout"] == 600


async def test_generate_category_knowledge_web_search_default_is_true(monkeypatch):
    calls = _patch_generate(monkeypatch)

    await generate_category_knowledge("робот-пылесос")

    assert calls[0]["cli_tools"] == "WebSearch"


async def test_generate_category_knowledge_no_web_search_plain_call(monkeypatch):
    calls = _patch_generate(monkeypatch)

    await generate_category_knowledge("робот-пылесос", web_search=False)

    assert len(calls) == 1
    assert _WEB_RESEARCH_GUIDE not in calls[0]["prompt"]
    assert "cli_tools" not in calls[0]
    assert "cli_timeout" not in calls[0]


# --- category_type robustness -------------------------------------------------

_ADAPALENE = {"canonical": "адапален", "aliases": ["adapalene"]}


def test_category_type_nested_in_model_book_is_hoisted():
    """Live opus answer (2026-10-13, «средства от акне») put category_type inside
    model_book; the top-level default must not silently win."""
    result = CategoryKnowledgeResult.model_validate(
        {
            "brand_book": {},
            "model_book": {"entries": [], "category_type": "composition_driven"},
            "ingredient_book": {"substances": [_ADAPALENE]},
        }
    )
    assert result.category_type == "composition_driven"


async def test_non_empty_ingredient_book_implies_composition_driven(monkeypatch):
    async def _fake_generate(prompt, response_model, **kwargs):
        return CategoryKnowledgeResult.model_validate(
            {"brand_book": {}, "model_book": {}, "ingredient_book": {"substances": [_ADAPALENE]}}
        )

    monkeypatch.setattr(generate_module, "generate", _fake_generate)

    _, _, category_type, _ = await generate_category_knowledge("средства от акне")

    assert category_type == "composition_driven"
