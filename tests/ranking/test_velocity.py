from datetime import UTC, datetime, timedelta

from buysmarter.ranking.velocity import review_velocity

NOW = datetime(2026, 10, 13, tzinfo=UTC)


def test_returns_none_when_no_base_snapshot_is_old_enough() -> None:
    history = [(NOW - timedelta(days=3), 100)]

    assert review_velocity(history, now_count=110, now=NOW) is None


def test_returns_none_for_empty_history() -> None:
    assert review_velocity([], now_count=110, now=NOW) is None


def test_negative_delta_is_clamped_to_zero() -> None:
    history = [(NOW - timedelta(days=14), 120)]

    assert review_velocity(history, now_count=100, now=NOW) == 0.0


def test_gain_over_two_weeks_is_reviews_per_week() -> None:
    history = [(NOW - timedelta(days=14), 100)]

    assert review_velocity(history, now_count=114, now=NOW) == 7.0


def test_picks_most_recent_eligible_base_snapshot() -> None:
    history = [
        (NOW - timedelta(days=28), 80),
        (NOW - timedelta(days=14), 100),
        (NOW - timedelta(days=3), 110),  # too recent to be a base
    ]

    assert review_velocity(history, now_count=114, now=NOW) == 7.0
