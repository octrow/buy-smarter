"""Pure ranking engine: hard filters + explainable multi-axis scoring.

No DB, no LLM, no network. See docs/PLAN.md "Universal pipeline" step 4-5
and docs/INIT_DATA.md "Core Principles" (Explainability, Confidence,
Alternatives) for the contract this module implements.
"""

import math
import statistics
from collections import Counter
from datetime import date
from typing import TYPE_CHECKING, NamedTuple

from buysmarter.ranking.delivery import (
    DELIVERY_LATER_CREDIT,
    delivery_note,
    is_fast_bucket,
    today_almaty,
)
from buysmarter.ranking.filters import apply_hard_filters, feature_present
from buysmarter.ranking.flags import (
    DUP_PRICE_GAP_CON,
    MIMIC_BRAND_CON,
    SUSPICIOUS_PRICE_CON,
    UNVERIFIED_FEATURE_CON,
)
from buysmarter.ranking.ingredient_axis import (
    build_ingredient_context,
    equivalence_verdicts,
    ingredient_cons,
    required_concentration,
    score_ingredient,
)
from buysmarter.ranking.model_tokens import extract_model_tokens, has_model_match
from buysmarter.ranking.models import (
    AxisScore,
    Candidate,
    IngredientContext,
    MarketContext,
    QueryConstraints,
    Recommendation,
    ScoredCandidate,
)
from buysmarter.ranking.selection import assemble_recommendation
from buysmarter.ranking.spec_conflicts import spec_conflicts

if TYPE_CHECKING:
    pass

# Axis weights, sum to 100. No config system in v1 — edit here to retune.
# v2 (docs/PLAN.md "Brand knowledge"): brand is a leading axis; marketplace
# reviews/popularity are down-weighted because noname brands inflate them.
DEFAULT_WEIGHTS: dict[str, float] = {
    "brand": 25.0,
    "price": 25.0,
    "specs": 20.0,
    "reviews": 12.0,
    "popularity": 8.0,
    "availability": 10.0,
}

# Composition-driven categories (cosmetics/pharma, docs/PLAN.md): the active
# substance is a leading axis and brand drops sharply, since a cheap generic with
# the same substance+concentration is genuinely equivalent to the pricey brand.
# Sum to 100, same as DEFAULT_WEIGHTS.
COMPOSITION_WEIGHTS: dict[str, float] = {
    "ingredient": 25.0,
    "price": 25.0,
    "specs": 15.0,
    "brand": 10.0,
    "reviews": 12.0,
    "popularity": 3.0,
    "availability": 10.0,
}

# Brand-trust multipliers by tier; unknown brands get a strong penalty since we
# have no trust signal to lean on.
_BRAND_TIER_FACTORS: dict[str, float] = {"A": 1.0, "B": 0.8, "C": 0.55}
_UNKNOWN_BRAND_FACTOR = 0.15

# Review-credibility multipliers (docs/PLAN.md "Ranking-фиксы"): marketplace
# reviews/popularity are exactly what noname brands inflate, so their signal
# strength scales with brand trust, not just the brand axis itself. Applied
# to the reviews and popularity axis scores. Static factor for v1; per-review
# fake-review heuristics (rating dispersion, date clustering) are a later
# iteration (see PLAN.md).
_CREDIBILITY_FACTORS: dict[str, float] = {"A": 1.0, "B": 1.0, "C": 0.9}
_UNKNOWN_CREDIBILITY_FACTOR = 0.4


def credibility_factor(candidate: Candidate) -> float:
    tier = candidate.brand_tier
    if tier is None:
        return _UNKNOWN_CREDIBILITY_FACTOR
    return _CREDIBILITY_FACTORS.get(tier, _UNKNOWN_CREDIBILITY_FACTOR)


KIMOVIL_INDEX_MAX = 100.0


def _tiered_prices(candidates: list[Candidate]) -> list[int]:
    """Sorted valid prices of candidates with a known brand tier."""
    return sorted(c.price for c in candidates if c.brand_tier is not None and c.price > 0)


def _max_reviews_by_source(candidates: list[Candidate]) -> dict[str, int]:
    """Largest review count per source, over candidates that report one."""
    by_source: dict[str, int] = {}
    for c in candidates:
        if c.review_count is not None:
            by_source[c.source] = max(by_source.get(c.source, 0), c.review_count)
    return by_source


def compute_market_context(candidates: list[Candidate]) -> MarketContext:
    """Aggregate pool-wide price/popularity stats for relative scoring.

    ``trusted_median_price`` is the median price among candidates with a
    known brand tier — the reference used by the price axis so a cheap
    noname can no longer set the bar for "full price credit". ``None`` when
    no candidate has a tier (price axis then falls back to ``median_price``).
    """
    # price <= 0 is an invalid listing: it must not drag min/median down.
    prices = sorted(c.price for c in candidates if c.price > 0) or [0]
    review_counts = [c.review_count for c in candidates if c.review_count is not None]
    tiered_prices = _tiered_prices(candidates)
    trusted_median = int(statistics.median(tiered_prices)) if tiered_prices else None
    return MarketContext(
        min_price=prices[0],
        median_price=statistics.median(prices),
        trusted_median_price=trusted_median,
        tiered_pool_size=len(tiered_prices),
        max_review_count=max(review_counts) if review_counts else 0,
        max_review_count_by_source=_max_reviews_by_source(candidates),
        pool_size=len(candidates),
    )


def _score_brand(candidate: Candidate, weights: dict[str, float]) -> AxisScore:
    weight = weights["brand"]
    tier = candidate.brand_tier
    factor = _BRAND_TIER_FACTORS.get(tier) if tier else None
    if factor is None:
        return AxisScore(
            name="brand",
            score=round(weight * _UNKNOWN_BRAND_FACTOR, 2),
            max_score=weight,
            explanation="unknown brand — no trust data",
        )
    label = candidate.brand or tier
    return AxisScore(
        name="brand",
        score=round(weight * factor, 2),
        max_score=weight,
        explanation=f"{label} is a tier-{tier} brand",
    )


# Price axis reference points, as a fraction of the reference median.
# Full credit at/below _PRICE_FULL_CREDIT_RATIO; linear decay above that down
# to _PRICE_FLOOR_RATIO once price reaches _PRICE_FLOOR_AT_RATIO * median.
_PRICE_FULL_CREDIT_RATIO = 0.6
_PRICE_FLOOR_AT_RATIO = 2.0
_PRICE_FLOOR_RATIO = 0.1

# Anti-counterfeit signal (docs/INIT_DATA.md): an unknown brand priced far
# below the trusted median is suspicious, not a bargain. Static threshold for
# now; LLM-based outlier verification is a later iteration.
_SUSPICIOUS_PRICE_RATIO = 0.4
_SUSPICIOUS_PRICE_PENALTY = 0.3


def _price_reference(market: MarketContext) -> tuple[float, str]:
    if market.trusted_median_price is not None:
        return market.trusted_median_price, "trusted-brand"
    return market.median_price, "pool"


# A price reference built on fewer than this many tiered prices is too thin
# to trust at face value (docs/PLAN.md "Ranking-фиксы"): one or two known-brand
# candidates setting the whole market's price bar is a fragile snapshot, not a
# real market read — and ZERO tiered candidates is worse still, since the pool
# median it silently falls back to (``_price_reference``) carries no brand
# signal whatsoever.
_DEGENERATE_TRUSTED_MEDIAN_MIN = 3
_DEGENERATE_CONFIDENCE_CAP = 0.7


def _is_degenerate_price_reference(market: MarketContext) -> bool:
    """True whenever the price axis's reference (trusted median OR, with zero
    tiered candidates, the pool-median fallback) is too thin to trust — caps
    confidence regardless of which reference ``_price_reference`` picked."""
    return market.tiered_pool_size < _DEGENERATE_TRUSTED_MEDIAN_MIN


def _price_reference_reliability_note(market: MarketContext) -> str | None:
    """Honesty suffix for the price-axis explanation when the reference is
    shaky, or ``None`` when the reference is solid (>= 3 tiered candidates).

    Zero tiered candidates silently falls back to the pool median
    (``_price_reference``) with no brand signal at all; 1-2 is a trusted
    median too thin to be a real market read.
    """
    if market.tiered_pool_size == 0:
        return " (нет товаров с известным брендом — референс по всему пулу)"
    if market.tiered_pool_size < _DEGENERATE_TRUSTED_MEDIAN_MIN:
        return f" (медиана по {market.tiered_pool_size} товарам с известным брендом — ненадёжна)"
    return None


def _score_price(
    candidate: Candidate, market: MarketContext, weights: dict[str, float]
) -> AxisScore:
    weight = weights["price"]
    if candidate.price <= 0:
        return AxisScore(name="price", score=0.0, max_score=weight, explanation="invalid price")
    reference, label = _price_reference(market)
    full_credit_at = _PRICE_FULL_CREDIT_RATIO * reference
    floor_at = _PRICE_FLOOR_AT_RATIO * reference
    if candidate.price <= full_credit_at:
        credit = 1.0
    elif candidate.price >= floor_at:
        credit = _PRICE_FLOOR_RATIO
    else:
        span = floor_at - full_credit_at
        decay = (candidate.price - full_credit_at) / span * (1.0 - _PRICE_FLOOR_RATIO)
        credit = 1.0 - decay
    # Explanation shows the actual price/reference ratio, not the post-cap
    # credit fraction — "ratio 1.00" for a price well below the reference
    # (full-credit already reached) used to read as "price == median".
    price_ratio = candidate.price / reference if reference else 0.0
    explanation = (
        f"price {candidate.price} vs {label} median {reference:.0f} (ratio {price_ratio:.2f})"
    )
    note = _price_reference_reliability_note(market)
    if note:
        explanation += note
    return AxisScore(
        name="price",
        score=round(weight * credit, 2),
        max_score=weight,
        explanation=explanation,
    )


# Price-trend signal (docs/PLAN.md фаза 4 "history цен"). A candidate priced
# below its own 30-day average is a genuine dip worth a small nudge; above it, a
# small penalty. # ponytail: simple ±7% thresholds, the quality of a discount is not scored;
# retune after 50 real runs or at the first miss seen in one.
_PRICE_HISTORY_LOW_RATIO = 0.93
_PRICE_HISTORY_HIGH_RATIO = 1.07
_PRICE_HISTORY_ADJUST = 0.10  # ±10% of the price-axis score, capped at max_score
_PRICE_HISTORY_PRO = "цена ниже средней"
_PRICE_HISTORY_CON = "цена выше средней"


def _apply_price_history(axis: AxisScore, candidate: Candidate) -> AxisScore:
    """Fold the 30-day price trend into the price axis (explanation + small nudge)."""
    hist = candidate.price_history
    if hist is None or hist.avg <= 0:
        return axis
    pct = (candidate.price - hist.avg) / hist.avg
    explanation = (
        f"{axis.explanation}; средняя за 30д: {hist.avg:,.0f} ₸ (сейчас {pct * 100:+.0f}%)"
    )
    score = axis.score
    if candidate.price < _PRICE_HISTORY_LOW_RATIO * hist.avg:
        score = min(axis.score * (1 + _PRICE_HISTORY_ADJUST), axis.max_score)
    elif candidate.price > _PRICE_HISTORY_HIGH_RATIO * hist.avg:
        score = axis.score * (1 - _PRICE_HISTORY_ADJUST)
    return AxisScore(
        name=axis.name, score=round(score, 2), max_score=axis.max_score, explanation=explanation
    )


def _price_history_verdict(candidate: Candidate) -> tuple[str, str] | None:
    """Pro/con for the price trend: ("pro"|"con", label), or ``None`` when neutral."""
    hist = candidate.price_history
    if hist is None or hist.avg <= 0:
        return None
    if candidate.price < _PRICE_HISTORY_LOW_RATIO * hist.avg:
        return "pro", _PRICE_HISTORY_PRO
    if candidate.price > _PRICE_HISTORY_HIGH_RATIO * hist.avg:
        return "con", _PRICE_HISTORY_CON
    return None


def _is_suspicious_price(candidate: Candidate, market: MarketContext) -> bool:
    """Unknown brand priced far below the trusted median — anti-counterfeit."""
    if candidate.brand_tier is not None or market.trusted_median_price is None:
        return False
    return candidate.price < _SUSPICIOUS_PRICE_RATIO * market.trusted_median_price


def _score_reviews(
    candidate: Candidate, market: MarketContext, weights: dict[str, float]
) -> AxisScore:
    weight = weights["reviews"]
    if candidate.rating is None or candidate.review_count is None:
        return AxisScore(
            name="reviews",
            score=0.0,
            max_score=weight,
            explanation="no rating/review data available",
        )
    rating_component = candidate.rating / 5.0
    # Volume is log-scaled against the busiest listing from the candidate's OWN
    # source, mirroring the popularity axis: Ozon's RU-wide review counters (tens
    # of thousands) must not set the bar for Kaspi's local 50-200 counts on a
    # shared axis (docs/PLAN.md "Ranking-фиксы"). Each candidate is compared only
    # to the most-reviewed listing from the same marketplace.
    source_max = market.max_review_count_by_source.get(candidate.source, 0)
    if source_max <= 0:
        volume_component = 0.0
    else:
        reviews = max(candidate.review_count, 0)
        volume_component = min(math.log1p(reviews) / math.log1p(source_max), 1.0)
    credibility = credibility_factor(candidate)
    score_val = weight * rating_component * volume_component * credibility
    explanation = f"rating {candidate.rating}/5 across {candidate.review_count} reviews"
    if credibility < 1.0:
        explanation += (
            " (discounted: unverified brand)"
            if candidate.brand_tier is None
            else f" (discounted: tier-{candidate.brand_tier} brand)"
        )
    return AxisScore(
        name="reviews",
        score=round(score_val, 2),
        max_score=weight,
        explanation=explanation,
    )


def _score_specs(
    candidate: Candidate, constraints: QueryConstraints, weights: dict[str, float]
) -> AxisScore:
    weight = weights["specs"]
    required = constraints.required_features
    if not required:
        coverage = 1.0 if candidate.specs else 0.5
        explanation = "no required features specified; " + (
            "spec data present" if candidate.specs else "no spec data"
        )
    else:
        matched = sum(1 for f in required if feature_present(f, candidate))
        coverage = matched / len(required)
        explanation = f"{matched}/{len(required)} required features matched"
    if candidate.kimovil_index is not None:
        # Smartphones: how good the model is, not only whether its specs are known.
        coverage = (coverage + candidate.kimovil_index / KIMOVIL_INDEX_MAX) / 2
        explanation += f"; Kimovil {candidate.kimovil_index:.0f}/100"
    return AxisScore(
        name="specs", score=round(weight * coverage, 2), max_score=weight, explanation=explanation
    )


def _score_popularity(
    candidate: Candidate, market: MarketContext, weights: dict[str, float]
) -> AxisScore:
    weight = weights["popularity"]
    if market.pool_size == 1:
        # A pool of one has nothing to be "more popular" than — half credit,
        # not "100% of pool max" (which reads as a false 8/8 "strong" signal).
        return AxisScore(
            name="popularity",
            score=round(weight * 0.5, 2),
            max_score=weight,
            explanation="single candidate — no pool comparison",
        )
    if candidate.review_count is None:
        return AxisScore(
            name="popularity",
            score=0.0,
            max_score=weight,
            explanation="no review-count data for popularity",
        )
    # Popularity is normalized WITHIN the candidate's own source: Ozon's RU-wide
    # review counters (tens of thousands) must not crush Kaspi's local 50-200
    # counts on a shared axis (docs/PLAN.md "Ranking-фиксы"). Each candidate is
    # compared only to the busiest listing from the same marketplace.
    source_max = market.max_review_count_by_source.get(candidate.source, 0)
    if source_max <= 0:
        return AxisScore(
            name="popularity",
            score=0.0,
            max_score=weight,
            explanation="no popularity data across pool",
        )
    percentile = min(max(candidate.review_count, 0) / source_max, 1.0)
    credibility = credibility_factor(candidate)
    explanation = (
        f"{candidate.review_count} reviews = {percentile:.0%} of "
        f"{candidate.source} pool max ({source_max})"
    )
    if candidate.review_velocity is not None:
        # ponytail: shown, not scored — snapshots are irregular until refresh runs on a schedule.
        explanation += f", +{candidate.review_velocity:g} отзывов/нед"
    if credibility < 1.0:
        explanation += (
            " (discounted: unverified brand)"
            if candidate.brand_tier is None
            else f" (discounted: tier-{candidate.brand_tier} brand)"
        )
    return AxisScore(
        name="popularity",
        score=round(weight * percentile * credibility, 2),
        max_score=weight,
        explanation=explanation,
    )


def _score_availability(candidate: Candidate, weights: dict[str, float], today: date) -> AxisScore:
    weight = weights["availability"]
    if not candidate.availability:
        return AxisScore(
            name="availability", score=0.0, max_score=weight, explanation="out of stock"
        )
    note = delivery_note(candidate, today)
    if note is None:
        return AxisScore(
            name="availability", score=weight, max_score=weight, explanation="in stock"
        )
    bucket, label = note
    credit = DELIVERY_LATER_CREDIT if bucket == "later" else 1.0
    return AxisScore(
        name="availability",
        score=round(weight * credit, 2),
        max_score=weight,
        explanation=f"in stock; {label}",
    )


def _confidence(candidate: Candidate) -> float:
    """Confidence = share of data-bearing fields we actually had to score with."""
    fields_present = [
        candidate.rating is not None,
        candidate.review_count is not None,
        candidate.brand_tier is not None,
        candidate.url is not None,
        bool(candidate.specs),
    ]
    return round(sum(fields_present) / len(fields_present), 2)


def _pros_cons(axes: list[AxisScore]) -> tuple[list[str], list[str]]:
    pros, cons = [], []
    for axis in axes:
        if axis.max_score <= 0:
            continue
        ratio = axis.score / axis.max_score
        if ratio >= 0.8:
            pros.append(f"strong {axis.name}")
        elif ratio <= 0.3:
            cons.append(f"weak {axis.name}")
    return pros, cons


# Soft penalty when the query names a model code (e.g. "F300") and the listing
# carries none of them: a different model in the same category, not a hard reject.
_MODEL_MISMATCH_FACTOR = 0.85
_MODEL_MISMATCH_CON = "другая модель: в названии нет «{token}»"


def _unverified_feature_cons(candidate: Candidate, constraints: QueryConstraints) -> list[str]:
    """Cons for required features we couldn't check because specs are empty.

    Mirrors the hard-filter carve-out in ``apply_hard_filters``: an empty
    specs dict means the data wasn't fetched, not that the feature is
    absent, so the candidate survives filtering but must be flagged.
    """
    if candidate.specs:
        return []
    return [
        UNVERIFIED_FEATURE_CON.format(feature=f)
        for f in constraints.required_features
        if not feature_present(f, candidate)
    ]


class _PriceFlags(NamedTuple):
    """Which counterfeit signals fire on a candidate's price."""

    dup_gap: bool
    mimic: bool
    generic: bool

    @property
    def suspicious(self) -> bool:
        return self.dup_gap or self.mimic or self.generic


def _price_flags(candidate: Candidate, market: MarketContext) -> _PriceFlags:
    # A dup-price-gap is only a counterfeit signal when this candidate is the
    # cheap side of the market too — a normally-priced listing whose duplicate
    # is simply overpriced (e.g. 23k vs a 62k re-lister) must not eat the hit.
    dup_gap = candidate.dup_price_gap is not None and candidate.price < _price_reference(market)[0]
    return _PriceFlags(
        dup_gap=dup_gap,
        mimic=candidate.mimic_brand is not None,
        generic=_is_suspicious_price(candidate, market),
    )


def _penalize_suspicious_price(
    price_axis: AxisScore, candidate: Candidate, flags: _PriceFlags
) -> AxisScore:
    # A brand-mimicry candidate's only edge is usually its cheap price — the
    # counterfeit signal also drives the price-axis penalty, same as the
    # dup-gap and unknown-brand-vs-median cases (docs/PLAN.md "Ranking-фиксы").
    if not flags.suspicious:
        return price_axis
    if flags.dup_gap:
        price_note = DUP_PRICE_GAP_CON.format(gap=candidate.dup_price_gap)
    elif flags.mimic:
        price_note = MIMIC_BRAND_CON.format(brand=candidate.mimic_brand)
    else:
        price_note = SUSPICIOUS_PRICE_CON
    return AxisScore(
        name="price",
        score=round(price_axis.score * _SUSPICIOUS_PRICE_PENALTY, 2),
        max_score=price_axis.max_score,
        explanation=price_axis.explanation + "; " + price_note,
    )


def _price_cons(candidate: Candidate, flags: _PriceFlags) -> list[str]:
    cons: list[str] = []
    if flags.dup_gap:
        cons.append(DUP_PRICE_GAP_CON.format(gap=candidate.dup_price_gap))
    elif flags.generic:
        cons.append(SUSPICIOUS_PRICE_CON)
    if flags.mimic:
        cons.append(MIMIC_BRAND_CON.format(brand=candidate.mimic_brand))
    return cons


def _score_axes(
    candidate: Candidate,
    constraints: QueryConstraints,
    market: MarketContext,
    price_axis: AxisScore,
    weights: dict[str, float],
    ingredient_ctx: IngredientContext | None,
    today: date,
) -> list[AxisScore]:
    axes: list[AxisScore] = []
    if ingredient_ctx is not None:
        axes.append(
            score_ingredient(
                candidate, ingredient_ctx, weights, required_concentration(constraints)
            )
        )
    return axes + [
        _score_brand(candidate, weights),
        price_axis,
        _score_reviews(candidate, market, weights),
        _score_specs(candidate, constraints, weights),
        _score_popularity(candidate, market, weights),
        _score_availability(candidate, weights, today),
    ]


def _verdict_pros_cons(
    axes: list[AxisScore],
    candidate: Candidate,
    constraints: QueryConstraints,
    flags: _PriceFlags,
    ingredient_ctx: IngredientContext | None,
    missed_model: str | None,
    today: date,
) -> tuple[list[str], list[str]]:
    pros, cons = _pros_cons(axes)
    # The only source of a "weak brand" con is an unknown brand (factor 0.15).
    cons = ["неизвестный бренд" if c == "weak brand" else c for c in cons]
    cons += _price_cons(candidate, flags)
    verdict = _price_history_verdict(candidate)
    if verdict is not None:
        (pros if verdict[0] == "pro" else cons).append(verdict[1])
    cons.extend(_unverified_feature_cons(candidate, constraints))
    if ingredient_ctx is not None:
        pros.extend(equivalence_verdicts(candidate, ingredient_ctx))
        cons = ingredient_cons(cons, candidate, ingredient_ctx)
    if missed_model is not None:
        cons.append(_MODEL_MISMATCH_CON.format(token=missed_model))
    cons.extend(spec_conflicts(candidate.title, candidate.specs))
    note = delivery_note(candidate, today) if candidate.availability else None
    if note is not None and is_fast_bucket(note[0]):
        pros.append(note[1])
    return pros, cons


def _score_confidence(candidate: Candidate, market: MarketContext) -> float:
    confidence = _confidence(candidate)
    if _is_degenerate_price_reference(market):
        # A market snapshot built on <3 tiered prices (including zero — the
        # silent pool-median fallback) can't support full confidence — the
        # reference itself is shaky, not just this candidate.
        confidence = min(confidence, _DEGENERATE_CONFIDENCE_CAP)
    return confidence


def score(
    candidate: Candidate,
    constraints: QueryConstraints,
    market: MarketContext,
    *,
    weights: dict[str, float] = DEFAULT_WEIGHTS,
    ingredient_ctx: IngredientContext | None = None,
    today: date | None = None,
    model_tokens: tuple[str, ...] = (),
) -> ScoredCandidate:
    """Score one candidate against pool-relative market context.

    ``ingredient_ctx`` switches on the composition-driven ingredient axis (and the
    caller passes ``COMPOSITION_WEIGHTS``); left ``None`` for spec-driven categories,
    which keep the exact original axis set and weights. ``today`` (Asia/Almaty) feeds
    the delivery grouping; ``model_tokens`` are the query's model codes (see
    ``model_tokens.extract_model_tokens``), empty means no model relevance check.
    """
    today = today or today_almaty()
    flags = _price_flags(candidate, market)
    price_axis = _penalize_suspicious_price(
        _apply_price_history(_score_price(candidate, market, weights), candidate), candidate, flags
    )
    axes = _score_axes(candidate, constraints, market, price_axis, weights, ingredient_ctx, today)
    total = round(sum(a.score for a in axes), 2)
    model_miss = bool(model_tokens) and not has_model_match(candidate, model_tokens)
    if model_miss:
        total = round(total * _MODEL_MISMATCH_FACTOR, 2)
    pros, cons = _verdict_pros_cons(
        axes,
        candidate,
        constraints,
        flags,
        ingredient_ctx,
        model_tokens[0] if model_miss else None,
        today,
    )
    return ScoredCandidate(
        candidate=candidate,
        total_score=total,
        confidence=_score_confidence(candidate, market),
        axes=axes,
        pros=pros,
        cons=cons,
    )


def _reason_key(reason: str) -> str:
    """Compact, aggregable bucket for a hard-filter rejection reason: everything
    before the first ':' (the stable prefix each check writes), or the whole
    string when there is none (already stable, e.g. "not available")."""
    return reason.split(":", 1)[0].strip()


def rank(
    candidates: list[Candidate],
    constraints: QueryConstraints,
    *,
    composition: bool = False,
    required_substances: list[str] | None = None,
    substances_verified: bool = False,
    query: str | None = None,
) -> Recommendation:
    """Filter, score, sort, and label the best + alternatives.

    ``query`` is the raw user request; its model codes (e.g. "F300") feed the soft
    model-relevance penalty. None or a category-only query means no effect.

    ``composition`` (docs/PLAN.md "Composition-driven категории") switches to the
    ingredient-weighted axes: ``required_substances`` are the canonical substances
    the query targets, and an in-pool equivalence context is built so generic
    analogs surface. ``substances_verified`` (extraction ran) additionally makes a
    required substance a hard filter — a candidate whose состав proves it absent is
    rejected. Spec-driven ranking is the default and is unchanged.

    Raises ValueError if no candidate survives hard filtering.
    """
    passed, rejected = apply_hard_filters(
        candidates,
        constraints,
        required_substances=required_substances,
        substances_verified=substances_verified,
    )
    if not passed:
        raise ValueError("no candidates passed hard filters")

    market = compute_market_context(passed)
    weights = COMPOSITION_WEIGHTS if composition else DEFAULT_WEIGHTS
    ingredient_ctx = (
        build_ingredient_context(passed, required_substances or []) if composition else None
    )
    model_tokens = tuple(extract_model_tokens(query) if query else ())
    scored = [
        score(
            c,
            constraints,
            market,
            weights=weights,
            ingredient_ctx=ingredient_ctx,
            model_tokens=model_tokens,
        )
        for c in passed
    ]
    reasons = dict(Counter(_reason_key(reason) for _, reason in rejected))
    return assemble_recommendation(scored, len(rejected), reasons)
