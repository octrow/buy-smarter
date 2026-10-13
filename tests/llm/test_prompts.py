"""parse_query must consciously pick the cheap free-stack combo."""

from buysmarter.llm import prompts
from buysmarter.ranking.models import QueryConstraints


async def test_parse_query_passes_free_stack_model(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        captured["model"] = model
        return QueryConstraints(category="irrigator", city="Almaty")

    monkeypatch.setattr(prompts, "generate", _fake_generate)

    result = await prompts.parse_query("нужен ирригатор")

    assert captured["model"] == "free-stack" == prompts.QUERY_PARSE_MODEL
    assert isinstance(result, QueryConstraints)


def test_parse_query_prompt_requires_russian_category() -> None:
    """Category must be forced into Russian — the marketplaces' language (live bug:

    query "SPF 50" (language-neutral) was parsed to English category
    "sunscreen", which returns zero results on Kaspi/Ozon/OLX (all Russian).
    """
    prompt = prompts.parse_query_prompt("SPF 50")

    assert "RUSSIAN" in prompt
    assert "TRANSLATE" in prompt
    assert "required_features" in prompt and "VERBATIM" in prompt


def test_parse_query_prompt_keeps_required_features_verbatim_example() -> None:
    """The one-shot example must show a verbatim SPF-style feature, not translated."""
    prompt = prompts.parse_query_prompt("SPF 50 sunscreen for face")

    assert '"required_features": ["SPF 50"]' in prompt
    assert "солнцезащитный крем" in prompt  # example category is in Russian


async def test_parse_query_prompt_reaches_generate_unmodified(monkeypatch) -> None:
    """The language instruction must actually reach the LLM call, not just exist."""
    captured: dict = {}

    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        captured["prompt"] = prompt
        return QueryConstraints(category="солнцезащитный крем", city=None)

    monkeypatch.setattr(prompts, "generate", _fake_generate)

    await prompts.parse_query("SPF 50")

    assert "RUSSIAN" in captured["prompt"]


def test_parse_query_prompt_keeps_named_model_in_category() -> None:
    """Live 2026-10-13: «iphone 13 б/у» parsed to category "смартфон" — the
    translate-neutral-forms rule ate the model, so every source searched "смартфон"."""
    prompt = prompts.parse_query_prompt("iphone 13 б/у")

    assert '"category": "смартфон iPhone 13"' in prompt
    assert "condition" in prompt.lower() and "б/у" in prompt
