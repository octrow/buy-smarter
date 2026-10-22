"""Tests for delivery-date grouping and the availability axis. Pure, no network."""

from datetime import date

from buysmarter.ranking.delivery import delivery_bucket, delivery_note, format_cost
from buysmarter.ranking.engine import DEFAULT_WEIGHTS, _score_availability
from buysmarter.ranking.models import Candidate

TODAY = date(2026, 10, 13)


def _candidate(**overrides) -> Candidate:
    defaults = dict(
        source="kaspi",
        source_product_id="1",
        title="Irrigator",
        price=10_000,
        availability=True,
        delivery_date=None,
        delivery_cost=None,
    )
    defaults.update(overrides)
    return Candidate(**defaults)


def test_bucket_is_today_for_same_day():
    assert delivery_bucket("2026-10-13", TODAY) == "today"


def test_bucket_is_tomorrow_for_next_day():
    assert delivery_bucket("2026-10-14", TODAY) == "tomorrow"


def test_bucket_is_later_from_day_after_tomorrow():
    assert delivery_bucket("2026-10-15", TODAY) == "later"


def test_bucket_treats_past_date_as_today():
    assert delivery_bucket("2026-10-12", TODAY) == "today"


def test_bucket_is_none_for_unknown_or_malformed_date():
    assert delivery_bucket(None, TODAY) is None
    assert delivery_bucket("not-a-date", TODAY) is None


def test_bucket_day_diff_crosses_month_boundary():
    assert delivery_bucket("2026-11-01", date(2026, 10, 31)) == "tomorrow"


def test_format_cost_free_zero_and_thousands_separator():
    assert format_cost(0.0) == "бесплатно"
    assert format_cost(1500.0) == "1 500 ₸"
    assert format_cost(None) is None


def test_note_tomorrow_free_delivery_label():
    bucket, label = delivery_note(_candidate(delivery_date="2026-10-14", delivery_cost=0.0), TODAY)
    assert bucket == "tomorrow"
    assert label == "доставка завтра (14 окт), бесплатно"


def test_note_later_label_with_cost_and_no_weekday_word():
    bucket, label = delivery_note(
        _candidate(delivery_date="2026-10-17", delivery_cost=1500.0), TODAY
    )
    assert bucket == "later"
    assert label == "доставка 17 окт, 1 500 ₸"


def test_note_today_label_without_cost_when_cost_unknown():
    assert delivery_note(_candidate(delivery_date="2026-10-13"), TODAY) == (
        "today",
        "доставка сегодня (13 окт)",
    )


def test_note_is_none_without_date():
    assert delivery_note(_candidate(delivery_cost=0.0), TODAY) is None


def test_availability_full_credit_for_tomorrow_with_label():
    axis = _score_availability(
        _candidate(delivery_date="2026-10-14", delivery_cost=0.0),
        DEFAULT_WEIGHTS,
        TODAY,
    )
    assert axis.score == DEFAULT_WEIGHTS["availability"]
    assert axis.max_score == DEFAULT_WEIGHTS["availability"]
    assert axis.explanation == "in stock; доставка завтра (14 окт), бесплатно"


def test_availability_reduced_credit_for_later_delivery():
    axis = _score_availability(_candidate(delivery_date="2026-10-20"), DEFAULT_WEIGHTS, TODAY)
    assert 0 < axis.score < DEFAULT_WEIGHTS["availability"]
    assert axis.max_score == DEFAULT_WEIGHTS["availability"]


def test_availability_unknown_date_keeps_legacy_explanation_and_credit():
    axis = _score_availability(_candidate(), DEFAULT_WEIGHTS, TODAY)
    assert axis.score == DEFAULT_WEIGHTS["availability"]
    assert axis.explanation == "in stock"


def test_availability_out_of_stock_stays_zero_even_with_date():
    axis = _score_availability(
        _candidate(availability=False, delivery_date="2026-10-13"), DEFAULT_WEIGHTS, TODAY
    )
    assert axis.score == 0.0
    assert axis.explanation == "out of stock"


def test_fast_delivery_becomes_a_pro_on_the_scored_candidate():
    from buysmarter.ranking.engine import score
    from buysmarter.ranking.models import MarketContext, QueryConstraints

    market = MarketContext(
        min_price=10_000,
        median_price=10_000.0,
        trusted_median_price=None,
        max_review_count=0,
        pool_size=2,
    )
    fast = _candidate(delivery_date="2026-10-14", delivery_cost=0.0)
    slow = _candidate(delivery_date="2026-10-20", delivery_cost=1500.0)

    constraints = QueryConstraints(search_phrase="ирригатор")
    fast_scored = score(fast, constraints, market, today=TODAY)
    slow_scored = score(slow, constraints, market, today=TODAY)

    assert "доставка завтра (14 окт), бесплатно" in fast_scored.pros
    assert not any("доставка" in p for p in slow_scored.pros)
