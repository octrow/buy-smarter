"""Post-parse cleanup of required features (``_drop_category_implied_features``)."""

from buysmarter.ranking.models import QueryConstraints
from buysmarter.services.recommend import _drop_category_implied_features


def _features(category: str, features: list[str]) -> list[str]:
    constraints = QueryConstraints(search_phrase=category, required_features=features)
    return _drop_category_implied_features(constraints).required_features


def test_single_token_in_category_dropped() -> None:
    assert _features("TWS наушники", ["TWS", "ANC"]) == ["ANC"]


def test_multiword_model_already_in_category_dropped() -> None:
    """Live 2026-10-13: «iphone 13 б/у» → required ["iphone 13", ...]; the model is the
    category, string-matching it as a spec just rejects on-category listings."""
    assert _features("смартфон iPhone 13", ["iPhone 13", "128 ГБ"]) == ["128 ГБ"]


def test_condition_words_are_not_features() -> None:
    """«б/у» is not a spec: it would reject every new Kaspi product; the used
    market is OLX's job, which always runs."""
    assert _features("смартфон iPhone 13", ["б/у", "Б/У", "used", "новый", "IPX7"]) == ["IPX7"]


def test_a_dropped_condition_word_sets_the_condition() -> None:
    """Decision 18: «б/у» leaves the features but is no longer lost."""
    used = QueryConstraints(search_phrase="iphone 13", required_features=["б/у", "128 ГБ"])
    new = QueryConstraints(search_phrase="iphone 13", required_features=["новый"])

    assert _drop_category_implied_features(used).condition == "used"
    assert _drop_category_implied_features(new).condition == "new"


def test_the_new_fields_default_to_a_plain_product_request() -> None:
    parsed = QueryConstraints(search_phrase="мужские кроссовки")

    assert parsed.intent == "product" and parsed.options == [] and parsed.restricted is None
    assert parsed.product_type == "" and parsed.budget_min is None


def test_the_llm_schema_has_no_category() -> None:
    """The LLM fills ``search_phrase``; ``category`` is gone from the parse."""
    assert "category" not in QueryConstraints.model_json_schema()["properties"]
