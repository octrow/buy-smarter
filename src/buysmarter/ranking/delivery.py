"""Delivery-date grouping for the availability axis (Asia/Almaty calendar days).

Pure except ``today_almaty()``. ``delivery_date`` arrives as an ISO date already
in Asia/Almaty (see market_sources kaspi parse); only the day difference matters.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from buysmarter.ranking.models import Candidate

ALMATY_TZ = ZoneInfo("Asia/Almaty")

# Share of the availability weight kept for a delivery later than tomorrow.
# Today/tomorrow keep the full credit an in-stock listing already gets.
DELIVERY_LATER_CREDIT = 0.6

_FAST_BUCKETS = ("today", "tomorrow")
_RU_MONTHS_GEN = (
    "янв",
    "фев",
    "мар",
    "апр",
    "май",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
)
_FREE_DELIVERY = "бесплатно"


def today_almaty() -> date:
    return datetime.now(ALMATY_TZ).date()


def delivery_bucket(delivery_date: str | None, today: date) -> str | None:
    """'today' | 'tomorrow' | 'later' by calendar-day diff; None when date unknown."""
    if delivery_date is None:
        return None
    try:
        delivered = date.fromisoformat(delivery_date)
    except ValueError:
        return None
    days = (delivered - today).days
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return "later"


def format_cost(cost: float | None) -> str | None:
    if cost is None:
        return None
    if cost <= 0:
        return _FREE_DELIVERY
    return f"{round(cost):,}".replace(",", " ") + " ₸"


def _short_date(delivery_date: str) -> str:
    d = date.fromisoformat(delivery_date)
    return f"{d.day} {_RU_MONTHS_GEN[d.month - 1]}"


def _head(bucket: str, delivery_date: str) -> str:
    when = _short_date(delivery_date)
    if bucket == "today":
        return f"доставка сегодня ({when})"
    if bucket == "tomorrow":
        return f"доставка завтра ({when})"
    return f"доставка {when}"


def delivery_note(candidate: Candidate, today: date) -> tuple[str, str] | None:
    """(bucket, Russian label) for a candidate with a known delivery date, else None."""
    bucket = delivery_bucket(candidate.delivery_date, today)
    if bucket is None or candidate.delivery_date is None:
        return None
    parts = [_head(bucket, candidate.delivery_date)]
    cost = format_cost(candidate.delivery_cost)
    if cost is not None:
        parts.append(cost)
    return bucket, ", ".join(parts)


def is_fast_bucket(bucket: str) -> bool:
    return bucket in _FAST_BUCKETS
