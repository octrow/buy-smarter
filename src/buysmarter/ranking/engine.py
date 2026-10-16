"""Pure ranking engine: hard filters + explainable multi-axis scoring.

No DB, no LLM, no network. See docs/PLAN.md "Universal pipeline" step 4-5
and docs/INIT_DATA.md "Core Principles" (Explainability, Confidence,
Alternatives) for the contract this module implements.
"""

import math
import re
import statistics
from collections import Counter
from datetime import date
from math import ceil
from typing import TYPE_CHECKING

from buysmarter.knowledge.ingredients import composition_text, normalize_concentration
from buysmarter.ranking.delivery import (
    DELIVERY_LATER_CREDIT,
    delivery_note,
    is_fast_bucket,
    today_almaty,
)
from buysmarter.ranking.model_tokens import extract_model_tokens, has_model_match
from buysmarter.ranking.models import (
    AxisScore,
    Candidate,
    EquivalenceGroup,
    IngredientContext,
    MarketContext,
    QueryConstraints,
    Recommendation,
    ScoredCandidate,
)
from buysmarter.ranking.spec_conflicts import spec_conflicts

if TYPE_CHECKING:
    from buysmarter.llm.review_summary import RequirementCheck, ReviewSummary

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


def _credibility_factor(candidate: Candidate) -> float:
    tier = candidate.brand_tier
    if tier is None:
        return _UNKNOWN_CREDIBILITY_FACTOR
    return _CREDIBILITY_FACTORS.get(tier, _UNKNOWN_CREDIBILITY_FACTOR)


# Waterproof-rating synonyms Kaspi listings use instead of the "IPX<n>" code
# itself. Static bridge for v1 — real LLM/embedding-based feature matching is
# a later iteration (see docs/PLAN.md).
KIMOVIL_INDEX_MAX = 100.0

_WATERPROOF_SYNONYMS = ("влагозащит", "водонепрониц", "waterproof")
_IPX_FEATURE_RE = re.compile(r"^ipx\d$")


def _normalize_for_match(text: str) -> str:
    """Lowercase and drop hyphens/spaces so "IPX-7" / "IPX 7" / "ipx7" align."""
    return text.lower().replace("-", "").replace(" ", "")


def _feature_present(feature: str, candidate: Candidate) -> bool:
    """Normalized substring match against title + spec values.

    Both sides are lowercased with hyphens/spaces stripped, so "IPX7" matches
    "IPX-7" or "ip x7". For an "ipx<n>" style feature, also accept the
    waterproof synonyms marketplaces use instead of the raw code (e.g.
    "Влагозащита", "водонепроницаемый"). Ceiling of v1: still string
    matching, not semantic. Upgrade path is LLM/embedding-based feature
    matching once this becomes the bottleneck.
    """
    needle = _normalize_for_match(feature)
    haystacks = [candidate.title, *candidate.specs.values()]
    if any(needle in _normalize_for_match(h) for h in haystacks):
        return True
    if _IPX_FEATURE_RE.match(needle):
        return any(syn in h.lower() for h in haystacks for syn in _WATERPROOF_SYNONYMS)
    return False


# Public name for services that check required features against a candidate.
feature_present = _feature_present

# Reason prefix for a candidate rejected because it lacks the explicitly required
# active substance (composition-driven categories). The caller keys the honest
# empty-pool message off this prefix, so keep them in sync.
NO_SUBSTANCE_REASON = "нет требуемого вещества"


def apply_hard_filters(
    candidates: list[Candidate],
    constraints: QueryConstraints,
    *,
    required_substances: list[str] | None = None,
    substances_verified: bool = False,
) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    """Split candidates into (passed, rejected-with-reason).

    Checks, in order: budget, excluded brand, availability, required
    features (minus a bare concentration when a substance is required),
    required active substance. First failing check wins the reason.

    The active-substance check only bites when ``substances_verified`` (i.e.
    ingredient extraction actually ran) and the candidate *declares* a состав
    that does not contain any ``required_substances`` — proven absence. A
    candidate with no состав text at all is a data gap, not a proven absence, so
    it survives (scored with an "unconfirmed" con), never silently rejected.
    """
    excluded_brands = {b.lower() for b in constraints.excluded_brands}
    required_substances = required_substances or []
    # With a required substance, a bare concentration ("0,1%") is graded on the
    # ingredient axis (``_required_concentration``), not string-matched here.
    hard_features = [
        f
        for f in constraints.required_features
        if not (required_substances and _CONCENTRATION_RE.fullmatch(f.strip()))
    ]
    passed: list[Candidate] = []
    rejected: list[tuple[Candidate, str]] = []

    for candidate in candidates:
        if constraints.budget_max is not None and candidate.price > constraints.budget_max:
            rejected.append(
                (
                    candidate,
                    f"price exceeds budget: {candidate.price} > {constraints.budget_max}",
                )
            )
            continue
        if candidate.brand and candidate.brand.lower() in excluded_brands:
            rejected.append((candidate, f"excluded brand: '{candidate.brand}'"))
            continue
        if constraints.trusted_brands_only and candidate.brand_tier is None:
            rejected.append((candidate, "unknown brand (trusted-brands-only)"))
            continue
        if not candidate.availability:
            rejected.append((candidate, "not available"))
            continue
        # An empty specs dict means specs weren't fetched (not that the
        # product lacks the feature) — don't reject on unverifiable data;
        # `score()` attaches a "не удалось проверить" con instead.
        if candidate.specs:
            missing = [f for f in hard_features if not _feature_present(f, candidate)]
            if missing:
                rejected.append((candidate, f"missing required feature(s): {', '.join(missing)}"))
                continue
        if (
            required_substances
            and substances_verified
            and _substance_proven_absent(candidate, required_substances)
        ):
            rejected.append((candidate, f"{NO_SUBSTANCE_REASON}: {', '.join(required_substances)}"))
            continue
        passed.append(candidate)

    return passed, rejected


def _has_required_substance(candidate: Candidate, required_substances: list[str]) -> bool:
    """True when the candidate's detected substances include a required one."""
    return any(s in candidate.detected_substances for s in required_substances)


def _substance_proven_absent(candidate: Candidate, required_substances: list[str]) -> bool:
    """A required substance is *proven* absent: состав declared, substance not in it.

    No состав text means the data is missing (unverifiable), not that the
    substance is absent — that candidate is kept and flagged, never rejected.
    """
    if _has_required_substance(candidate, required_substances):
        return False
    return bool(composition_text(candidate.specs))


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
    tiered_prices = sorted(c.price for c in candidates if c.brand_tier is not None and c.price > 0)
    trusted_median = int(statistics.median(tiered_prices)) if tiered_prices else None
    by_source: dict[str, int] = {}
    for c in candidates:
        if c.review_count is not None:
            by_source[c.source] = max(by_source.get(c.source, 0), c.review_count)
    return MarketContext(
        min_price=prices[0],
        median_price=statistics.median(prices),
        trusted_median_price=trusted_median,
        tiered_pool_size=len(tiered_prices),
        max_review_count=max(review_counts) if review_counts else 0,
        max_review_count_by_source=by_source,
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
# The con carried by a candidate flagged for a suspiciously low price. Also used
# to exclude such candidates from every alternative role (they stay in the
# ranked table with the con visible, but are never endorsed as a "best X").
_SUSPICIOUS_PRICE_CON = "подозрительно низкая цена для категории"
# A same-product dedupe finding a much pricier duplicate elsewhere is evidence
# on its own — brand tier is irrelevant, so the con names the gap directly
# rather than reusing the generic unknown-brand wording. Only bites when the
# candidate itself is priced BELOW the market reference: at/above it, the
# pricier duplicate is the outlier (an overpriced listing), not this one.
_DUP_PRICE_GAP_MARKER = "ниже другого листинга этого же товара"
_DUP_PRICE_GAP_CON = "цена в {gap:.1f} раза " + _DUP_PRICE_GAP_MARKER + " — проверьте продавца"
# A title mimicking a known brand without a genuine match (e.g. spaced-out
# "J BL" for JBL) is a counterfeit signal independent of price.
_MIMIC_BRAND_CON = "возможная подделка под {brand}"


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
    credibility = _credibility_factor(candidate)
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
        matched = sum(1 for f in required if _feature_present(f, candidate))
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
    credibility = _credibility_factor(candidate)
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

_UNVERIFIED_FEATURE_CON = "не удалось проверить: {feature}"


def _unverified_feature_cons(candidate: Candidate, constraints: QueryConstraints) -> list[str]:
    """Cons for required features we couldn't check because specs are empty.

    Mirrors the hard-filter carve-out in ``apply_hard_filters``: an empty
    specs dict means the data wasn't fetched, not that the feature is
    absent, so the candidate survives filtering but must be flagged.
    """
    if candidate.specs:
        return []
    return [
        _UNVERIFIED_FEATURE_CON.format(feature=f)
        for f in constraints.required_features
        if not _feature_present(f, candidate)
    ]


# --- Ingredient axis (composition-driven categories, docs/PLAN.md) ---------
# Credit for the active-substance axis: required substance present with a matching
# (or simply known) concentration is full credit; present but unknown/mismatched
# concentration is a partial 0.7; absent is 0.
_INGREDIENT_PRESENT_UNKNOWN_CONC = 0.7
# Composition unconfirmed (no состав declared, extraction had nothing to read):
# honest partial credit — a data gap is not proven absence (proven-absent
# candidates are hard-filtered out before scoring).
_INGREDIENT_UNCONFIRMED_CREDIT = 0.3
_CONCENTRATION_RE = re.compile(r"\d+[.,]?\d*\s*%")


def _required_concentration(constraints: QueryConstraints) -> str | None:
    """A concentration named in the query's required features (e.g. "0.1%"), or None."""
    for feature in constraints.required_features:
        match = _CONCENTRATION_RE.search(feature)
        if match:
            return match.group(0)
    return None


def build_ingredient_context(
    candidates: list[Candidate], required_substances: list[str]
) -> IngredientContext:
    """Pool-wide equivalence groups for the ingredient axis (pure, docs/PLAN.md).

    Targets the query's required substances, falling back to every substance seen
    in the pool. A group is recorded only when two+ candidates share the substance
    at different prices — that price gap is what powers the generic-equivalence
    "аналог по действующему веществу" advice.
    """
    present = {s for c in candidates for s in c.detected_substances}
    targets = [s for s in required_substances if s in present] or sorted(present)
    groups: dict[str, EquivalenceGroup] = {}
    for substance in targets:
        members = [c for c in candidates if substance in c.detected_substances and c.price > 0]
        if len(members) < 2:
            continue
        cheapest = min(members, key=lambda c: c.price)
        priciest = max(members, key=lambda c: c.price)
        if cheapest.price >= priciest.price:
            continue
        groups[substance] = EquivalenceGroup(
            cheapest_id=(cheapest.source, cheapest.source_product_id),
            cheapest_name=cheapest.title,
            cheapest_price=cheapest.price,
            priciest_id=(priciest.source, priciest.source_product_id),
            priciest_name=priciest.title,
            priciest_price=priciest.price,
            size=len(members),
        )
    return IngredientContext(required_substances=required_substances, groups=groups)


def _score_ingredient(
    candidate: Candidate,
    ctx: IngredientContext,
    weights: dict[str, float],
    required_concentration: str | None,
) -> AxisScore:
    """Score the active-substance match: present+concentration full, unknown 0.7, absent 0."""
    weight = weights["ingredient"]
    required = ctx.required_substances
    present = (
        any(s in candidate.detected_substances for s in required)
        if required
        else bool(candidate.detected_substances)
    )
    if not present:
        # No состав declared → unconfirmed (data gap), partial credit; состав
        # declared but substance not in it → proven absent, zero.
        if not composition_text(candidate.specs):
            return AxisScore(
                name="ingredient",
                score=round(weight * _INGREDIENT_UNCONFIRMED_CREDIT, 2),
                max_score=weight,
                explanation="состав не подтверждён — вещество не проверено",
            )
        return AxisScore(
            name="ingredient",
            score=0.0,
            max_score=weight,
            explanation="требуемое действующее вещество отсутствует",
        )
    conc = candidate.concentration
    if conc is None:
        credit, why = _INGREDIENT_PRESENT_UNKNOWN_CONC, "вещество есть, концентрация неизвестна"
    elif required_concentration and normalize_concentration(conc) != normalize_concentration(
        required_concentration
    ):
        credit = _INGREDIENT_PRESENT_UNKNOWN_CONC
        why = f"вещество есть, концентрация {conc} ≠ {required_concentration}"
    else:
        credit, why = 1.0, f"действующее вещество присутствует ({conc})"
    return AxisScore(
        name="ingredient", score=round(weight * credit, 2), max_score=weight, explanation=why
    )


def _equivalence_verdicts(candidate: Candidate, ctx: IngredientContext) -> list[str]:
    """Generic-equivalence pros: cheapest generic vs the pricey brand (docs/PLAN.md)."""
    identity = (candidate.source, candidate.source_product_id)
    pros: list[str] = []
    for substance, group in ctx.groups.items():
        if substance not in candidate.detected_substances:
            continue
        if tuple(group.cheapest_id) == identity:
            pros.append(f"дженерик-аналог {group.priciest_name} по той же формуле")
        elif candidate.price > group.cheapest_price:
            ratio = candidate.price / group.cheapest_price
            pros.append(
                f"аналог по действующему веществу: {group.cheapest_name} — "
                f"в {ratio:.1f} раз дешевле"
            )
    return list(dict.fromkeys(pros))  # dedupe, keep order (multi-substance candidates)


def _ingredient_cons(cons: list[str], candidate: Candidate, ctx: IngredientContext) -> list[str]:
    """Swap the generic "weak ingredient" con for an honest composition con.

    Proven-absent candidates are hard-filtered out before scoring, so a
    "not present" candidate reaching here is one whose состав we simply could
    not read — flag that as unconfirmed (honest uncertainty), not "weak".
    """
    cons = [c for c in cons if c != "weak ingredient"]
    required = ctx.required_substances
    if not required:
        return cons
    if not _has_required_substance(candidate, required) and not composition_text(candidate.specs):
        cons.append(f"состав не подтверждён: {', '.join(required)}?")
    return cons


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
    price_axis = _apply_price_history(_score_price(candidate, market, weights), candidate)
    # A dup-price-gap is only a counterfeit signal when this candidate is the
    # cheap side of the market too — a normally-priced listing whose duplicate
    # is simply overpriced (e.g. 23k vs a 62k re-lister) must not eat the hit.
    dup_gap_suspicious = (
        candidate.dup_price_gap is not None and candidate.price < _price_reference(market)[0]
    )
    mimic_suspicious = candidate.mimic_brand is not None
    generic_suspicious = _is_suspicious_price(candidate, market)
    # A brand-mimicry candidate's only edge is usually its cheap price — the
    # counterfeit signal also drives the price-axis penalty, same as the
    # dup-gap and unknown-brand-vs-median cases (docs/PLAN.md "Ranking-фиксы").
    suspicious = generic_suspicious or dup_gap_suspicious or mimic_suspicious
    if suspicious:
        if dup_gap_suspicious:
            price_note = _DUP_PRICE_GAP_CON.format(gap=candidate.dup_price_gap)
        elif mimic_suspicious:
            price_note = _MIMIC_BRAND_CON.format(brand=candidate.mimic_brand)
        else:
            price_note = _SUSPICIOUS_PRICE_CON
        price_axis = AxisScore(
            name="price",
            score=round(price_axis.score * _SUSPICIOUS_PRICE_PENALTY, 2),
            max_score=price_axis.max_score,
            explanation=price_axis.explanation + "; " + price_note,
        )
    axes: list[AxisScore] = []
    if ingredient_ctx is not None:
        axes.append(
            _score_ingredient(
                candidate, ingredient_ctx, weights, _required_concentration(constraints)
            )
        )
    axes += [
        _score_brand(candidate, weights),
        price_axis,
        _score_reviews(candidate, market, weights),
        _score_specs(candidate, constraints, weights),
        _score_popularity(candidate, market, weights),
        _score_availability(candidate, weights, today),
    ]
    total = round(sum(a.score for a in axes), 2)
    model_miss = bool(model_tokens) and not has_model_match(candidate, model_tokens)
    if model_miss:
        total = round(total * _MODEL_MISMATCH_FACTOR, 2)
    pros, cons = _pros_cons(axes)
    # The only source of a "weak brand" con is an unknown brand (factor 0.15).
    cons = ["неизвестный бренд" if c == "weak brand" else c for c in cons]
    if dup_gap_suspicious:
        cons.append(_DUP_PRICE_GAP_CON.format(gap=candidate.dup_price_gap))
    elif generic_suspicious:
        cons.append(_SUSPICIOUS_PRICE_CON)
    if candidate.mimic_brand is not None:
        cons.append(_MIMIC_BRAND_CON.format(brand=candidate.mimic_brand))
    verdict = _price_history_verdict(candidate)
    if verdict is not None:
        (pros if verdict[0] == "pro" else cons).append(verdict[1])
    cons.extend(_unverified_feature_cons(candidate, constraints))
    if ingredient_ctx is not None:
        pros.extend(_equivalence_verdicts(candidate, ingredient_ctx))
        cons = _ingredient_cons(cons, candidate, ingredient_ctx)
    if model_miss:
        cons.append(_MODEL_MISMATCH_CON.format(token=model_tokens[0]))
    cons.extend(spec_conflicts(candidate.title, candidate.specs))
    note = delivery_note(candidate, today) if candidate.availability else None
    if note is not None and is_fast_bucket(note[0]):
        pros.append(note[1])
    confidence = _confidence(candidate)
    if _is_degenerate_price_reference(market):
        # A market snapshot built on <3 tiered prices (including zero — the
        # silent pool-median fallback) can't support full confidence — the
        # reference itself is shaky, not just this candidate.
        confidence = min(confidence, _DEGENERATE_CONFIDENCE_CAP)
    return ScoredCandidate(
        candidate=candidate,
        total_score=total,
        confidence=confidence,
        axes=axes,
        pros=pros,
        cons=cons,
    )


_PREMIUM_TIERS = ("A", "B")

# best_value only qualifies candidates within this fraction of the best
# score — otherwise cheap junk (low score, tiny price) wins on score/price
# ratio alone regardless of how bad it actually is.
_BEST_VALUE_MIN_SCORE_RATIO = 0.8

# best_budget quality floor: a candidate must score at least this fraction of
# the best pick's total_score to qualify — otherwise the cheapest listing
# (possibly a counterfeit or scam) auto-wins on price alone regardless of how
# badly it scores everywhere else (docs/PLAN.md "Ranking-фиксы").
_BEST_BUDGET_MIN_SCORE_RATIO = 0.5


# Flavor/color vocabulary for ``variant_key``: a title differing only by these
# words is the same product in another flavor/color, not a distinct SKU.
# Prefixes cover Russian inflections (шоколадный/шоколадная); words that would
# over-match as a prefix (белый vs белок, серый vs сыворотка) are listed whole.
_VARIANT_PREFIXES = (
    "шоколад",
    "молочн",  # «молочный шоколад» — flavor, not a product line
    "ванил",
    "клубни",
    "банан",
    "карамел",
    "кокос",
    "печень",
    "фисташ",
    "вишн",
    "черник",
    "малин",
    "манг",
    "чёрн",
    "черн",
    "красн",
    "розов",
    "зелен",
    "зелён",
    "chocolate",
    "vanill",
    "strawberr",
    "banana",
    "caramel",
    "coconut",
    "cookie",
)
_VARIANT_WORDS = frozenset(
    {
        "вкус",
        "вкусом",
        "цвет",
        "цвета",
        "цветом",
        "белый",
        "белая",
        "белое",
        "синий",
        "синяя",
        "синее",
        "серый",
        "серая",
        "серое",
        "голубой",
        "голубая",
        "желтый",
        "жёлтый",
        "желтая",
        "жёлтая",
        "cream",
        "cookies",
        "black",
        "white",
        "blue",
        "pink",
        "gray",
        "grey",
        "green",
        "red",
        "yellow",
        "flavor",
        "flavour",
        "color",
        "colour",
        "и",
        "с",
        "and",
    }
)


def _is_variant_token(token: str) -> bool:
    return token in _VARIANT_WORDS or token.startswith(_VARIANT_PREFIXES)


def variant_key(title: str) -> str:
    """Title with flavor/color words and punctuation removed — two listings
    with equal keys are variants of one product (e.g. PhaseX chocolate vs
    banana-strawberry). Volume/weight tokens are kept: 930 г vs 2 кг differ."""
    tokens = re.sub(r"[^\w\s]", " ", title.lower()).split()
    return " ".join(t for t in tokens if not _is_variant_token(t))


def _identity(scored: ScoredCandidate) -> tuple[str, str]:
    """Product identity for dedup: same source + source_product_id."""
    return (scored.candidate.source, scored.candidate.source_product_id)


def _is_barred_from_roles(scored: ScoredCandidate) -> bool:
    """True when a candidate must never be endorsed as a "best X" alternative
    (nor, per ``assemble_recommendation``, crowned ``best`` outright).

    Covers both suspicious-price variants (generic unknown-brand con and the
    dup-price-gap con), brand-mimicry candidates, and a review-summary
    counterfeit verdict — all stay visible in the ranked table, but none may
    win best/best_value/best_budget/best_premium.
    """
    if scored.candidate.mimic_brand is not None:
        return True
    # Dup-price-gap bars by its CON, not the raw field — the gap only counts
    # as suspicion when ``score()`` deemed the candidate market-cheap too.
    if any(_DUP_PRICE_GAP_MARKER in c for c in scored.cons):
        return True
    if _COUNTERFEIT_CON in scored.cons or _COUNTERFEIT_CON_MULTI in scored.cons:
        return True
    return _SUSPICIOUS_PRICE_CON in scored.cons


def is_barred(scored: ScoredCandidate) -> bool:
    """Public wrapper for ``_is_barred_from_roles`` — for callers outside this
    module (e.g. CLI/report renderers flagging a listing with a "⚠" marker)
    that should not reach into a private name."""
    return _is_barred_from_roles(scored)


def _pick_alternatives(
    best: ScoredCandidate, pool: list[ScoredCandidate], full: list[ScoredCandidate]
) -> dict[str, ScoredCandidate]:
    """Pick role-labeled alternatives, each a distinct product from the best
    pick and from every other role — a role is dropped rather than repeat a
    product (e.g. a pool of one must not crown the best pick as its own
    "best value" alternative).
    """
    # Candidates flagged with a suspiciously-low price are barred from EVERY
    # alternative role: they remain in the ranked list/table with their con
    # visible, but must never be endorsed as "best budget/value/premium"
    # (docs/PLAN.md "Ranking-фиксы", anti-counterfeit).
    used = {_identity(best)}
    # Flavor/color variants of an already-picked product never take a role.
    used_variants = {variant_key(best.candidate.title)}
    eligible = [s for s in pool if _identity(s) not in used and not _is_barred_from_roles(s)]
    alternatives: dict[str, ScoredCandidate] = {}

    def _claim(role: str, candidates: list[ScoredCandidate], key, reverse: bool) -> None:
        remaining = [
            s
            for s in candidates
            if _identity(s) not in used and variant_key(s.candidate.title) not in used_variants
        ]
        if not remaining:
            return
        chosen = (max if reverse else min)(remaining, key=key)
        alternatives[role] = chosen
        used.add(_identity(chosen))
        used_variants.add(variant_key(chosen.candidate.title))

    # Only candidates close to the best score qualify for "best value" — a
    # low-scoring, suspiciously cheap product must not win on score/price
    # ratio alone.
    threshold = _BEST_VALUE_MIN_SCORE_RATIO * best.total_score
    # A price <= 0 is an invalid listing (see ``_score_price``): it would divide
    # by zero here and "win" best_budget as the cheapest, so it gets neither role.
    priced = [s for s in eligible if s.candidate.price > 0]
    value_candidates = [s for s in priced if s.total_score >= threshold]
    _claim(
        "best_value",
        value_candidates,
        key=lambda s: s.total_score / s.candidate.price,
        reverse=True,
    )

    # Quality floor: a candidate scoring far below the best pick must not win
    # best_budget on price alone (e.g. a counterfeit that scores 30/100).
    budget_threshold = _BEST_BUDGET_MIN_SCORE_RATIO * best.total_score
    budget_candidates = [s for s in priced if s.total_score >= budget_threshold]
    _claim("best_budget", budget_candidates, key=lambda s: s.candidate.price, reverse=False)

    # best_premium only ever goes to a known, trusted brand (tier A/B) — an
    # unknown-brand candidate ("noname KERN") must never win this role.
    # Prefer a premium-tier candidate from the top quartile of the full pool;
    # fall back to any remaining premium-tier candidate if the quartile has
    # none left (e.g. because the best pick itself was the sole tier-A/B
    # member of that quartile).
    quartile_n = max(1, ceil(len(full) * 0.25))
    top_quartile_ids = {
        _identity(s) for s in sorted(full, key=lambda s: s.total_score, reverse=True)[:quartile_n]
    }

    def _is_premium_tier(s: ScoredCandidate) -> bool:
        return s.candidate.brand_tier in _PREMIUM_TIERS

    premium_pool = [
        s for s in eligible if _is_premium_tier(s) and _identity(s) in top_quartile_ids
    ] or [s for s in eligible if _is_premium_tier(s)]
    _claim("best_premium", premium_pool, key=lambda s: s.candidate.price, reverse=True)

    return alternatives


def assemble_recommendation(
    scored: list[ScoredCandidate],
    rejected_count: int,
    rejected_reasons: dict[str, int] | None = None,
) -> Recommendation:
    """Sort scored candidates and label the best + role alternatives.

    ``best`` is the highest-scoring candidate NOT barred by
    ``_is_barred_from_roles`` (suspicious price, dup-price-gap, brand mimicry,
    or a review-summary counterfeit verdict) — a counterfeit's inflated
    score/price ratio must never crown it the top pick. Falls back to the raw
    top score (``scored[0]``) only when every candidate in the pool is barred,
    i.e. there is nothing trustworthy left to prefer.

    Split out of ``rank`` so a caller can re-assemble after a second pass tweaks
    scores (e.g. the review-trust re-score in ``services.recommend``) without
    re-running hard filters or the whole scoring pipeline — such callers pass
    the ORIGINAL ``rejected_reasons`` back through unchanged (re-scoring never
    changes which candidates were hard-filtered out).
    """
    scored = sorted(scored, key=lambda s: s.total_score, reverse=True)
    # A price <= 0 listing is invalid and must not be crowned; it is a last
    # resort, like barred ones, only when nothing else exists.
    best = next(
        (s for s in scored if not _is_barred_from_roles(s) and s.candidate.price > 0),
        None,
    ) or next((s for s in scored if not _is_barred_from_roles(s)), scored[0])
    pool = [s for s in scored if s is not best] or scored
    alternatives = _pick_alternatives(best, pool, scored)
    return Recommendation(
        best=best,
        alternatives=alternatives,
        ranked=scored,
        rejected_count=rejected_count,
        rejected_reasons=rejected_reasons or {},
    )


def apply_review_trust(scored: ScoredCandidate, trust: float, note: str) -> ScoredCandidate:
    """Return a copy of ``scored`` with its reviews axis rescaled by review trust.

    ``trust`` (0.3-1.0, from ``ranking.review_trust``) composes with the brand
    credibility factor already baked into the axis via ``min()`` — a fake-review
    penalty only tightens the factor, never loosens it. The trust note is
    appended to the reviews-axis explanation when ``trust < 1.0``.
    """
    credibility = _credibility_factor(scored.candidate)
    factor_ratio = min(credibility, trust) / credibility  # <= 1.0, credibility > 0
    new_axes: list[AxisScore] = []
    for axis in scored.axes:
        if axis.name != "reviews":
            new_axes.append(axis)
            continue
        explanation = axis.explanation
        if trust < 1.0:
            explanation += f"; review trust {trust:.1f}: {note}"
        new_axes.append(
            AxisScore(
                name="reviews",
                score=round(axis.score * factor_ratio, 2),
                max_score=axis.max_score,
                explanation=explanation,
            )
        )
    total = round(sum(a.score for a in new_axes), 2)
    return scored.model_copy(update={"axes": new_axes, "total_score": total})


# Review-summary penalty loop (docs/INIT_DATA.md anti-counterfeit / reliability):
# an LLM review summary can carry severity signals the star rating hides. A
# counterfeit-suspect listing is the strongest hit; a "serious" reliability
# concern (breaks early, unsafe, doesn't do its core job) is next; a "minor"
# concern only adds a con. Multipliers compose so a doubly-flagged listing eats
# both. Pure function — the caller re-sorts, so the best pick may change.
#
# Seller-multiplicity nuance: on Kaspi one card aggregates dozens of sellers
# (shown price = the cheapest), so a counterfeit complaint may indict a single
# bad seller, not the product. On a multi-seller card the counterfeit penalty
# softens (0.8 vs 0.6) and the con points at "some sellers", not the product;
# a single buy-box context (Ozon, or a 1-2 offer Kaspi card) keeps the full hit
# since the complaint is directly attributable.
_COUNTERFEIT_FACTOR = 0.6
_COUNTERFEIT_FACTOR_MULTI = 0.8
_RELIABILITY_SERIOUS_FACTOR = 0.85
_MULTI_SELLER_THRESHOLD = 3
_COUNTERFEIT_CON = "отзывы подозревают подделку"
_COUNTERFEIT_CON_MULTI = (
    "в отзывах жалобы на подделку у части продавцов — выбирайте продавца с высоким рейтингом"
)
_RELIABILITY_CON = "отзывы: надёжность под вопросом"


def _top_red_flag(summary: "ReviewSummary") -> str:
    """The headline red-flag con text (first structured flag, else a generic)."""
    return summary.red_flags[0] if summary.red_flags else _RELIABILITY_CON


def _counterfeit_penalty(candidate: Candidate) -> tuple[float, str]:
    """Counterfeit (factor, con) tuned by seller multiplicity of the card."""
    if candidate.offer_count > _MULTI_SELLER_THRESHOLD:
        return _COUNTERFEIT_FACTOR_MULTI, _COUNTERFEIT_CON_MULTI
    return _COUNTERFEIT_FACTOR, _COUNTERFEIT_CON


def apply_summary_penalty(scored: ScoredCandidate, summary: "ReviewSummary") -> ScoredCandidate:
    """Fold a review summary's severity signals into a candidate's score.

    Counterfeit suspicion scales score *and* confidence (0.6, or a softer 0.8 on
    a multi-seller Kaspi card — see module note); a "serious" reliability concern
    scales both by 0.85 and surfaces the top red flag as a con; a "minor" concern
    adds the con only. Factors compose. The subtracted points are recorded in
    ``review_penalty`` for display; the axis breakdown is left untouched. Returns
    ``scored`` unchanged when nothing applies.
    """
    if not summary.counterfeit_suspicion and summary.reliability_concern == "none":
        return scored
    factor = 1.0
    confidence = scored.confidence
    cons = list(scored.cons)
    if summary.counterfeit_suspicion:
        cf_factor, cf_con = _counterfeit_penalty(scored.candidate)
        factor *= cf_factor
        confidence *= cf_factor
        cons.append(cf_con)
    if summary.reliability_concern == "serious":
        factor *= _RELIABILITY_SERIOUS_FACTOR
        confidence *= _RELIABILITY_SERIOUS_FACTOR
        cons.append(_top_red_flag(summary))
    elif summary.reliability_concern == "minor":
        cons.append(_top_red_flag(summary))
    new_total = round(scored.total_score * factor, 2)
    penalty = round(scored.total_score - new_total, 2)
    return scored.model_copy(
        update={
            "total_score": new_total,
            "confidence": round(confidence, 2),
            "cons": cons,
            "review_penalty": round(scored.review_penalty + penalty, 2),
        }
    )


# Defect complaints in the reviews (``ranking.review_trust.count_defect_complaints``):
# two or more complaints about a defect/fake, covering at least a fifth of the
# sample, dent the total by 10%. Pure; the caller re-sorts.
_DEFECT_MIN_COMPLAINTS = 2
_DEFECT_MIN_SHARE = 0.2
_DEFECT_FACTOR = 0.9
_DEFECT_CON = "жалобы на брак в отзывах: {complaints} из {sample}"


def apply_defect_penalty(scored: ScoredCandidate, complaints: int, sample: int) -> ScoredCandidate:
    """Discount a candidate whose reviews keep complaining about defects.

    Returns ``scored`` itself when the complaints are below the floor or their
    share of ``sample`` is under ``_DEFECT_MIN_SHARE``. Otherwise the total is
    scaled by ``_DEFECT_FACTOR``, the subtracted points go to ``review_penalty``
    and a con names the count. Like ``apply_summary_penalty``, axes stay as-is.
    """
    if sample <= 0 or complaints < _DEFECT_MIN_COMPLAINTS:
        return scored
    if complaints / sample < _DEFECT_MIN_SHARE:
        return scored
    new_total = round(scored.total_score * _DEFECT_FACTOR, 2)
    penalty = round(scored.total_score - new_total, 2)
    con = _DEFECT_CON.format(complaints=complaints, sample=sample)
    return scored.model_copy(
        update={
            "total_score": new_total,
            "cons": [*scored.cons, con],
            "review_penalty": round(scored.review_penalty + penalty, 2),
        }
    )


# Review-verified requirements (``apply_requirement_checks``). A refuted feature
# is a stronger signal than a defect complaint, hence the steeper factor per check.
_REFUTED_FACTOR = 0.8
_CONFIRMED_PRO = "отзывы подтверждают: {feature} — «{quote}»"
_REFUTED_CON = "по отзывам нет: {feature} — «{quote}»"


def apply_requirement_checks(
    scored: ScoredCandidate, checks: list["RequirementCheck"]
) -> ScoredCandidate:
    """Turn review verdicts on required features into pros, cons and score.

    Confirmed: the "не удалось проверить" con for that feature is replaced by a
    pro quoting the reviews. Refuted: each one scales the total by
    ``_REFUTED_FACTOR`` (factors compose), the points go to ``review_penalty``
    and a con quotes the reviews. Insufficient verdicts change nothing. Returns
    ``scored`` itself when there is nothing to apply.
    """
    confirmed = [c for c in checks if c.verdict == "confirmed"]
    refuted = [c for c in checks if c.verdict == "refuted"]
    if not confirmed and not refuted:
        return scored
    resolved_cons = {_UNVERIFIED_FEATURE_CON.format(feature=c.feature) for c in confirmed}
    cons = [con for con in scored.cons if con not in resolved_cons]
    pros = [
        *scored.pros,
        *(_CONFIRMED_PRO.format(feature=c.feature, quote=c.quote) for c in confirmed),
    ]
    cons.extend(_REFUTED_CON.format(feature=c.feature, quote=c.quote) for c in refuted)
    new_total = scored.total_score
    if refuted:
        new_total = round(scored.total_score * _REFUTED_FACTOR ** len(refuted), 2)
    penalty = round(scored.total_score - new_total, 2)
    return scored.model_copy(
        update={
            "total_score": new_total,
            "pros": pros,
            "cons": cons,
            "review_penalty": round(scored.review_penalty + penalty, 2),
        }
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
