"""Counterfeit risk from how far a branded listing undercuts the genuine item's price.

A marketplace may sell a genuine item a little below the official store (sales,
seller promos), so "cheaper than official" alone proves nothing. The bigger the
gap, the likelier a fake: mild gaps lower confidence, deep ones bar the listing
from every role (docs/review/2026-10-21-2354/img_10-review.md, item 2). Used
marketplaces (OLX) are exempt: a private seller's price can be anything.
"""

from enum import Enum

from buysmarter.ranking.models import Candidate, MarketContext

# Listing price / genuine-item reference price.
# ponytail: static cut-offs picked from one sneaker run; retune on real misses.
LOW_RATIO = 0.7  # below: a noticeable gap, confidence capped
SUSPICIOUS_RATIO = 0.4  # below: likely fake, barred from roles
# Without brand knowledge the trusted-brand median mixes cheap and premium brands,
# so only a far deeper gap counts there.
MEDIAN_SUSPICIOUS_RATIO = 0.25
LOW_CONFIDENCE_CAP = 0.6
# Sources whose prices are private sellers' asks, not shop prices.
_PRIVATE_SOURCES = frozenset({"olx"})

GAP_CON = "цена {ratio:.0%} от цены оригинала ({kind}) — проверьте продавца"
_KIND_LABELS = {"model": "эта модель", "brand": "самый дешёвый оригинал бренда"}


class Risk(Enum):
    NONE = "none"
    LOW = "low"
    SUSPICIOUS = "suspicious"


def price_risk(candidate: Candidate, market: MarketContext) -> tuple[Risk, float | None]:
    """Risk level and the price/reference ratio behind it for a known-brand listing.

    Unknown brands are judged by ``engine._is_suspicious_price``; an official
    seller, a used market or a missing price carry no risk here.
    """
    if (
        candidate.brand_tier is None
        or candidate.official_seller
        or candidate.source in _PRIVATE_SOURCES
        or candidate.price <= 0
    ):
        return Risk.NONE, None
    if candidate.reference_price:
        ratio = candidate.price / candidate.reference_price
        if ratio < SUSPICIOUS_RATIO:
            return Risk.SUSPICIOUS, ratio
        return (Risk.LOW, ratio) if ratio < LOW_RATIO else (Risk.NONE, ratio)
    median = market.trusted_median_price
    if median:
        ratio = candidate.price / median
        if ratio < MEDIAN_SUSPICIOUS_RATIO:
            return Risk.SUSPICIOUS, ratio
    return Risk.NONE, None


def gap_con(candidate: Candidate, ratio: float) -> str:
    """The con naming a mild gap to the genuine item's price."""
    kind = _KIND_LABELS.get(candidate.reference_kind or "", "известные бренды")
    return GAP_CON.format(ratio=ratio, kind=kind)
