"""Review velocity: how fast a product collects reviews, as a demand signal.

Pure math over stored ``(captured_at, review_count)`` snapshots. Velocity is a
proxy for demand, not sales: marketplaces do not publish sales counts, and
review growth lags purchases. The base snapshot must be at least
``min_age_days`` old because day-over-day deltas are noise (review counts
arrive in bursts and get moderated); a week-or-longer window is the
kaspipulse methodology.

``now`` and snapshot timestamps must both be timezone-aware.
"""

from datetime import datetime, timedelta

DEFAULT_MIN_AGE_DAYS = 7
SECONDS_PER_DAY = 86_400
DAYS_PER_WEEK = 7
VELOCITY_DECIMALS = 2


def review_velocity(
    history: list[tuple[datetime, int]],
    now_count: int,
    now: datetime,
    min_age_days: int = DEFAULT_MIN_AGE_DAYS,
) -> float | None:
    """Return reviews gained per week, or ``None`` if no base snapshot is old enough.

    The base is the most recent snapshot at least ``min_age_days`` older than
    ``now``. A negative delta (moderation removed reviews) is reported as 0.0.
    """
    eligible = [
        (captured_at, count)
        for captured_at, count in history
        if (now - captured_at) >= timedelta(days=min_age_days) and now > captured_at
    ]
    if not eligible:
        return None
    base_at, base_count = max(eligible, key=lambda pair: pair[0])
    delta = max(0, now_count - base_count)
    weeks = (now - base_at).total_seconds() / SECONDS_PER_DAY / DAYS_PER_WEEK
    return round(delta / weeks, VELOCITY_DECIMALS)
