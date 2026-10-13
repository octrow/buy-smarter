"""Tests for model-token relevance (soft penalty, not a hard filter)."""

from buysmarter.ranking.engine import _MODEL_MISMATCH_CON, rank, score
from buysmarter.ranking.model_tokens import extract_model_tokens, has_model_match
from buysmarter.ranking.models import Candidate, MarketContext, QueryConstraints


def _candidate(title: str, specs: dict[str, str] | None = None, **overrides) -> Candidate:
    defaults = dict(
        source="kaspi",
        source_product_id=title,
        title=title,
        price=20_000,
        specs=specs or {},
        availability=True,
    )
    defaults.update(overrides)
    return Candidate(**defaults)


def test_extracts_letter_digit_tokens_and_drops_years():
    tokens = extract_model_tokens("ирригатор IPX7 тихий 2024 a54")
    assert tokens == ["ipx7", "a54"]


def test_compact_comparison_ignores_hyphens_and_spaces():
    tokens = extract_model_tokens("ipx-7")
    assert tokens == ["ipx7"]
    assert has_model_match(_candidate("Irrigator IPX 7 pro"), tokens)


def test_digit_only_tokens_are_not_model_codes():
    assert extract_model_tokens("дом 12 комнат 300") == []
    assert extract_model_tokens("F300") == ["f300"]


def test_budget_numbers_are_not_model_tokens():
    assert extract_model_tokens("до 15000 тенге") == []


def test_category_only_query_has_no_tokens_and_no_effect():
    constraints = QueryConstraints(category="ирригатор")
    scored = score(_candidate("Random irrigator"), constraints, _market())
    assert not any("другая модель" in c for c in scored.cons)


def test_mismatched_model_applies_factor_and_con():
    constraints = QueryConstraints(category="ирригатор")
    base = score(_candidate("Mijia F300 irrigator"), constraints, _market())
    other = score(
        _candidate("Mijia F200 irrigator"),
        constraints,
        _market(),
        model_tokens=("f300",),
    )
    matched = score(
        _candidate("Mijia F300 irrigator"),
        constraints,
        _market(),
        model_tokens=("f300",),
    )
    assert matched.total_score == base.total_score
    assert other.total_score == round(base.total_score * 0.85, 2)
    assert _MODEL_MISMATCH_CON.format(token="f300") in other.cons


def test_model_token_matches_specs_too():
    tokens = extract_model_tokens("a54 смартфон")
    candidate = _candidate("Смартфон Samsung", specs={"Модель": "A54"})
    assert has_model_match(candidate, tokens)


def test_rank_threads_query_into_scoring():
    constraints = QueryConstraints(category="ирригатор")
    candidates = [
        _candidate("Mijia F300 irrigator", source_product_id="a"),
        _candidate("Mijia F200 irrigator", source_product_id="b"),
    ]
    rec = rank(candidates, constraints, query="ирригатор F300")
    by_id = {s.candidate.source_product_id: s for s in rec.ranked}
    assert _MODEL_MISMATCH_CON.format(token="f300") in by_id["b"].cons
    assert _MODEL_MISMATCH_CON.format(token="f300") not in by_id["a"].cons


def _market() -> MarketContext:
    return MarketContext(
        min_price=20_000,
        median_price=20_000.0,
        trusted_median_price=None,
        max_review_count=0,
        pool_size=2,
    )


def test_cyrillic_unit_and_price_tokens_are_not_model_codes() -> None:
    assert extract_model_tokens("телефон 128гб до 40000тг") == []
