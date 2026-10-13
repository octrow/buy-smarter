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
