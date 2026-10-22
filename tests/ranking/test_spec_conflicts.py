"""Tests for the title-vs-spec unit conflict check (pure, conservative)."""

from buysmarter.ranking.spec_conflicts import spec_conflicts


def test_matching_capacity_has_no_conflict():
    assert spec_conflicts("Power bank 10000mAh", {"Ёмкость": "10 000 мАч"}) == []


def test_conflicting_capacity_yields_one_warning():
    warnings = spec_conflicts("Power bank 10000mAh", {"Ёмкость": "5000 мАч"})
    assert warnings == ["название: 10000 мАч, характеристики: 5000 мАч"]


def test_different_units_are_not_compared():
    assert spec_conflicts("Power bank 10000mAh", {"Мощность": "65 Вт"}) == []
    assert spec_conflicts("Бутылка 500 мл", {"Объём": "0.5 л"}) == []


def test_storage_conflict_in_gb_is_reported():
    assert spec_conflicts("Смартфон 128GB", {"Память": "8 ГБ"}) == [
        "название: 128 ГБ, характеристики: 8 ГБ"
    ]


def test_specs_without_units_are_ignored():
    assert spec_conflicts("Power bank 10000mAh", {"Ёмкость": "5000"}) == []


def test_tolerance_absorbs_rounding_within_one_percent():
    assert spec_conflicts("Power bank 10000mAh", {"Ёмкость": "10050 мАч"}) == []


def test_decimal_comma_in_title_matches_dot_in_spec():
    assert spec_conflicts("Смартфон 6,7 дюйм", {"Экран": "6.7 дюйма"}) == []


def test_title_unit_absent_means_no_check():
    assert spec_conflicts("Power bank", {"Ёмкость": "5000 мАч"}) == []


def test_unit_aliases_normalize_to_one_unit():
    assert spec_conflicts("Зарядка 65W", {"Мощность": "65 Вт"}) == []
    assert spec_conflicts("Зарядка 65W", {"Мощность": "30 W"}) == [
        "название: 65 Вт, характеристики: 30 Вт"
    ]


def test_score_adds_conflict_as_con_without_changing_total():
    from datetime import date

    from buysmarter.ranking.engine import score
    from buysmarter.ranking.models import Candidate, MarketContext, QueryConstraints

    market = MarketContext(
        min_price=20_000,
        median_price=20_000.0,
        trusted_median_price=None,
        max_review_count=0,
        pool_size=2,
    )
    constraints = QueryConstraints(search_phrase="power bank")
    base = Candidate(
        source="kaspi",
        source_product_id="1",
        title="Power bank 10000mAh",
        price=20_000,
        specs={"Ёмкость": "10000 мАч"},
    )
    clash = base.model_copy(update={"specs": {"Ёмкость": "5000 мАч"}})
    today = date(2026, 10, 13)

    clean = score(base, constraints, market, today=today)
    conflicting = score(clash, constraints, market, today=today)

    assert "название: 10000 мАч, характеристики: 5000 мАч" in conflicting.cons
    assert conflicting.total_score == clean.total_score
