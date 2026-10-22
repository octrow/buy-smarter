"""Post-parse cleanup of required features (``_drop_category_implied_features``)."""

from buysmarter.ranking.models import QueryConstraints
from buysmarter.services.recommend import _drop_category_implied_features


def _features(category: str, features: list[str]) -> list[str]:
    constraints = QueryConstraints(category=category, required_features=features)
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
    used = QueryConstraints(category="iphone 13", required_features=["б/у", "128 ГБ"])
    new = QueryConstraints(category="iphone 13", required_features=["новый"])

    assert _drop_category_implied_features(used).condition == "used"
    assert _drop_category_implied_features(new).condition == "new"


def test_category_and_search_phrase_stay_one_value() -> None:
    """Until ``category`` is removed, old callers and new ones read the same phrase."""
    old = QueryConstraints(category="мужские кроссовки")
    new = QueryConstraints(search_phrase="мужские кроссовки", product_type="кроссовки")

    assert old.search_phrase == old.category == "мужские кроссовки"
    assert new.category == "мужские кроссовки"
    assert old.intent == "product" and old.options == [] and old.restricted is None


def test_a_stored_parse_without_the_new_fields_validates() -> None:
    stored = {"category": "ирригатор", "city": "Almaty", "budget_max": 40000}

    parsed = QueryConstraints.model_validate(stored)

    assert parsed.search_phrase == "ирригатор"
    assert parsed.product_type == "" and parsed.budget_min is None


def test_the_llm_schema_has_no_category() -> None:
    """The LLM fills ``search_phrase``; a second name for it would invite two answers."""
    assert "category" not in QueryConstraints.model_json_schema()["properties"]
