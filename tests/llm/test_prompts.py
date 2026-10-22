"""parse_query must consciously pick the cheap free-stack combo."""

from buysmarter.llm import prompts
from buysmarter.ranking.models import QueryConstraints


async def test_parse_query_passes_free_stack_model(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        captured["model"] = model
        return QueryConstraints(search_phrase="irrigator", city="Almaty")

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
        return QueryConstraints(search_phrase="солнцезащитный крем", city=None)

    monkeypatch.setattr(prompts, "generate", _fake_generate)

    await prompts.parse_query("SPF 50")

    assert "RUSSIAN" in captured["prompt"]


def test_parse_query_prompt_keeps_named_model_in_category() -> None:
    """Live 2026-10-13: «iphone 13 б/у» parsed to category "смартфон" — the
    translate-neutral-forms rule ate the model, so every source searched "смартфон"."""
    prompt = prompts.parse_query_prompt("iphone 13 б/у")

    assert '"search_phrase": "смартфон iPhone 13"' in prompt
    assert "condition" in prompt.lower() and "б/у" in prompt


async def test_parse_query_reads_the_new_fields(monkeypatch) -> None:
    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return response_model.model_validate(
            {
                "intent": "multi",
                "search_phrase": "смартфон",
                "product_type": "смартфон",
                "options": ["iPhone 15", "Samsung S24"],
            }
        )

    monkeypatch.setattr(prompts, "generate", _fake_generate)

    parsed = await prompts.parse_query("iPhone 15 или S24")

    assert parsed.intent == "multi"
    assert parsed.options == ["iPhone 15", "Samsung S24"]
    assert parsed.search_phrase == "смартфон"


async def test_an_empty_llm_product_type_falls_back_to_the_naive_one(monkeypatch) -> None:
    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return response_model.model_validate({"search_phrase": "мужские кроссовки 42 размер"})

    monkeypatch.setattr(prompts, "generate", _fake_generate)

    parsed = await prompts.parse_query("мужские кроссовки 42 размер")

    assert parsed.product_type == "кроссовки"


def test_prompt_teaches_every_intent_and_the_budget_rules() -> None:
    prompt = prompts.parse_query_prompt("подарок для мамы")

    for word in ("product", "need", "open", "multi", "service", "unclear"):
        assert f'"intent": "{word}"' in prompt
    assert "шампунь от перхоти" in prompt  # a need with one product type: no question
    assert "product_type" in prompt and "restricted" in prompt and "condition" in prompt
    assert "до 2 м" in prompt and "4K" in prompt  # units and resolutions are not money


def test_naive_parse_is_a_product_with_a_bare_product_type() -> None:
    parsed = prompts.parse_query_naive("мужские кроссовки 42 размер до 50 штук")

    assert parsed.intent == "product"
    assert parsed.search_phrase == "мужские кроссовки 42 размер"
    assert parsed.product_type == "кроссовки"
    assert parsed.budget_max == 50_000


def test_naive_parse_without_letters_is_unclear() -> None:
    assert prompts.parse_query_naive("???").intent == "unclear"
    assert prompts.parse_query_naive("1").intent == "unclear"


def test_naive_parse_reads_the_condition() -> None:
    assert prompts.parse_query_naive("iphone 13 б/у").condition == "used"
    assert prompts.parse_query_naive("новый iphone 13").condition == "new"
    assert prompts.parse_query_naive("iphone 13").condition is None


def test_naive_parse_keeps_the_budget_range_and_note() -> None:
    assert prompts.parse_query_naive("ноутбук 30-40 тыс").budget_min == 30_000
    assert prompts.parse_query_naive("айфон до 500$").budget_note is not None
