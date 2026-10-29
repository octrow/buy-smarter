"""Tests for the pure ranking engine. No DB, no LLM, no network."""

import math

import pytest

from buysmarter.ranking.engine import (
    DEFAULT_WEIGHTS,
    NoCandidatesPassed,
    compute_market_context,
    rank,
    score,
)
from buysmarter.ranking.filters import _feature_present, apply_hard_filters
from buysmarter.ranking.flags import COUNTERFEIT_CON, SUSPICIOUS_PRICE_CON
from buysmarter.ranking.models import (
    AxisScore,
    Candidate,
    PriceHistory,
    QueryConstraints,
    ScoredCandidate,
)
from buysmarter.ranking.penalties import apply_review_trust
from buysmarter.ranking.selection import _pick_alternatives, assemble_recommendation, is_barred


def _candidate(**overrides) -> Candidate:
    defaults = dict(
        source="kaspi",
        source_product_id="1",
        title="Xiaomi Mijia F300 irrigator",
        brand="Xiaomi",
        brand_tier="C",
        price=35_000,
        rating=4.5,
        review_count=500,
        specs={"waterproof": "IPX7", "charging": "USB"},
        availability=True,
        url="https://kaspi.kz/shop/p/1",
    )
    defaults.update(overrides)
    if "title" not in overrides:
        # Distinct products need distinct titles: equal titles read as one
        # product's variants and are kept out of separate alternative roles.
        defaults["title"] = f"{defaults['title']} #{defaults['source_product_id']}"
    return Candidate(**defaults)


def _constraints(**overrides) -> QueryConstraints:
    defaults = dict(
        search_phrase="irrigator",
        city="Almaty",
        budget_max=40_000,
        required_features=["IPX7"],
        excluded_brands=[],
        preferred_marketplaces=["kaspi", "ozon"],
    )
    defaults.update(overrides)
    return QueryConstraints(**defaults)


# --- hard filters ---------------------------------------------------------


def test_hard_filter_rejects_over_budget():
    cheap = _candidate(source_product_id="1", price=30_000)
    expensive = _candidate(source_product_id="2", price=50_000)
    passed, rejected = apply_hard_filters([cheap, expensive], _constraints(budget_max=40_000))

    assert passed == [cheap]
    assert len(rejected) == 1
    assert rejected[0][0] is expensive
    assert "budget" in rejected[0][1]


def test_hard_filter_rejects_excluded_brand():
    allowed = _candidate(source_product_id="1", brand="Xiaomi")
    excluded = _candidate(source_product_id="2", brand="AcmeCheap")
    passed, rejected = apply_hard_filters(
        [allowed, excluded], _constraints(excluded_brands=["acmecheap"])
    )

    assert passed == [allowed]
    assert rejected[0][0] is excluded
    assert "excluded" in rejected[0][1]


def test_hard_filter_rejects_unavailable():
    available = _candidate(source_product_id="1", availability=True)
    unavailable = _candidate(source_product_id="2", availability=False)
    passed, rejected = apply_hard_filters(
        [available, unavailable], _constraints(required_features=[])
    )

    assert passed == [available]
    assert "not available" in rejected[0][1]


def test_hard_filter_rejects_missing_required_feature():
    has_feature = _candidate(source_product_id="1", specs={"waterproof": "IPX7"})
    missing_feature = _candidate(
        source_product_id="2", specs={"waterproof": "IPX4"}, title="Basic irrigator"
    )
    passed, rejected = apply_hard_filters(
        [has_feature, missing_feature], _constraints(required_features=["IPX7"])
    )

    assert passed == [has_feature]
    assert "missing required feature" in rejected[0][1]


# --- scoring monotonicity --------------------------------------------------


def test_cheaper_candidate_scores_higher_on_price_axis():
    cheap = _candidate(source_product_id="1", price=20_000)
    expensive = _candidate(source_product_id="2", price=40_000)
    market = compute_market_context([cheap, expensive])
    constraints = _constraints(required_features=[])

    cheap_scored = score(cheap, constraints, market)
    expensive_scored = score(expensive, constraints, market)

    cheap_price_axis = next(a for a in cheap_scored.axes if a.name == "price")
    expensive_price_axis = next(a for a in expensive_scored.axes if a.name == "price")
    assert cheap_price_axis.score > expensive_price_axis.score


def test_more_reviews_scores_higher_on_reviews_axis():
    fewer = _candidate(source_product_id="1", rating=4.5, review_count=10)
    more = _candidate(source_product_id="2", rating=4.5, review_count=5_000)
    market = compute_market_context([fewer, more])
    constraints = _constraints(required_features=[])

    fewer_scored = score(fewer, constraints, market)
    more_scored = score(more, constraints, market)

    fewer_reviews_axis = next(a for a in fewer_scored.axes if a.name == "reviews")
    more_reviews_axis = next(a for a in more_scored.axes if a.name == "reviews")
    assert more_reviews_axis.score > fewer_reviews_axis.score


def test_confidence_lower_when_fields_missing():
    complete = _candidate(source_product_id="1")
    sparse = _candidate(
        source_product_id="2",
        rating=None,
        review_count=None,
        brand=None,
        brand_tier=None,
        url=None,
        specs={},
    )
    market = compute_market_context([complete, sparse])
    constraints = _constraints(required_features=[])

    complete_scored = score(complete, constraints, market)
    sparse_scored = score(sparse, constraints, market)

    assert sparse_scored.confidence < complete_scored.confidence


def test_every_axis_has_non_empty_explanation():
    candidate = _candidate()
    market = compute_market_context([candidate])
    scored = score(candidate, _constraints(required_features=[]), market)

    assert len(scored.axes) == 6
    for axis in scored.axes:
        assert axis.explanation.strip() != ""


# --- brand axis ------------------------------------------------------------


def test_weights_sum_to_100():
    assert sum(DEFAULT_WEIGHTS.values()) == 100.0


def test_brand_tier_a_beats_c_beats_unknown():
    market = compute_market_context([_candidate()])
    constraints = _constraints(required_features=[])

    def _brand_axis(tier):
        cand = _candidate(brand_tier=tier)
        scored = score(cand, constraints, market)
        return next(a for a in scored.axes if a.name == "brand").score

    assert _brand_axis("A") > _brand_axis("C") > _brand_axis(None)


def test_unknown_brand_adds_con_and_lowers_confidence():
    # 3+ tiered candidates so the degenerate-trusted-median confidence cap
    # (< 3 tiered prices) doesn't flatten both candidates to the same 0.7.
    market = compute_market_context([_candidate(source_product_id=str(i)) for i in range(3)])
    constraints = _constraints(required_features=[])
    known = score(_candidate(brand_tier="A"), constraints, market)
    unknown = score(_candidate(brand="KERN", brand_tier=None), constraints, market)

    assert "неизвестный бренд" in unknown.cons
    assert unknown.confidence < known.confidence
    brand_axis = next(a for a in unknown.axes if a.name == "brand")
    assert "unknown brand" in brand_axis.explanation


def test_hard_filter_trusted_brands_only_rejects_unknown():
    known = _candidate(source_product_id="1", brand="Waterpik", brand_tier="A")
    noname = _candidate(source_product_id="2", brand="KERN", brand_tier=None)
    passed, rejected = apply_hard_filters(
        [known, noname], _constraints(required_features=[], trusted_brands_only=True)
    )

    assert passed == [known]
    assert rejected[0][0] is noname
    assert "unknown brand" in rejected[0][1]


# --- rank -------------------------------------------------------------------


def test_rank_returns_best_and_role_labeled_alternatives():
    # Four distinct passing candidates so best + all three alternative roles
    # can each land on a different product: best_fit wins overall, value_pick
    # has the best score/price ratio, budget_pick is cheapest, premium_pick
    # is the priciest tier-A/B product.
    best_fit = _candidate(
        source_product_id="1",
        price=25_000,
        rating=4.8,
        review_count=2_000,
        specs={"waterproof": "IPX7"},
        brand_tier="A",
    )
    value_pick = _candidate(
        source_product_id="2",
        price=16_000,
        rating=4.6,
        review_count=1_000,
        specs={"waterproof": "IPX7"},
        brand_tier="B",
    )
    budget_pick = _candidate(
        source_product_id="3",
        price=10_000,
        rating=3.0,
        review_count=20,
        specs={"waterproof": "IPX7"},
        brand_tier=None,
    )
    premium_pick = _candidate(
        source_product_id="4",
        price=38_000,
        rating=4.9,
        review_count=1_800,
        specs={"waterproof": "IPX7"},
        brand_tier="A",
    )
    rejected_candidate = _candidate(
        source_product_id="5", price=100_000, specs={"waterproof": "IPX7"}
    )

    recommendation = rank(
        [best_fit, value_pick, budget_pick, premium_pick, rejected_candidate],
        _constraints(budget_max=40_000, required_features=["IPX7"]),
    )

    assert recommendation.rejected_count == 1
    assert recommendation.best.candidate.source_product_id == "1"
    assert {"best_value", "best_budget", "best_premium"} <= recommendation.alternatives.keys()
    assert recommendation.alternatives["best_value"].candidate.source_product_id == "2"
    assert recommendation.alternatives["best_budget"].candidate.source_product_id == "3"
    assert recommendation.alternatives["best_premium"].candidate.source_product_id == "4"
    # No role ever repeats the best pick or another role's product.
    picked_ids = [recommendation.best.candidate.source_product_id] + [
        alt.candidate.source_product_id for alt in recommendation.alternatives.values()
    ]
    assert len(picked_ids) == len(set(picked_ids))


def test_rank_best_budget_is_cheapest_among_passing():
    # A cheap noname (unknown tier) shouldn't win overall now, but is still the
    # cheapest passing candidate → best_budget.
    cheap = _candidate(
        source_product_id="1", price=15_000, review_count=10, rating=3.0, brand_tier=None
    )
    mid = _candidate(source_product_id="2", price=25_000, review_count=500, rating=4.5)
    expensive = _candidate(source_product_id="3", price=38_000, review_count=1_000, rating=4.8)

    recommendation = rank([cheap, mid, expensive], _constraints(required_features=[]))

    budget_pick = recommendation.alternatives["best_budget"]
    assert budget_pick.candidate.source_product_id == "1"
    assert budget_pick.candidate.price == min(c.price for c in [cheap, mid, expensive])


def test_best_budget_dropped_when_the_best_pick_is_already_the_cheapest():
    # The pick undercuts everyone: a dearer leftover must not be labeled "budget".
    best = _candidate(source_product_id="1", price=15_000, review_count=1_000, rating=4.9)
    dearer = _candidate(source_product_id="2", price=25_000, review_count=500, rating=4.5)

    recommendation = rank([best, dearer], _constraints(required_features=[]))

    assert recommendation.best.candidate.source_product_id == "1"
    assert "best_budget" not in recommendation.alternatives


# --- trusted median / anti-counterfeit --------------------------------------


def test_trusted_median_price_used_when_tiers_present():
    noname = _candidate(source_product_id="1", price=4_184, brand="Noname", brand_tier=None)
    tiered_cheap = _candidate(source_product_id="2", price=20_000, brand_tier="B")
    tiered_expensive = _candidate(source_product_id="3", price=40_000, brand_tier="A")

    market = compute_market_context([noname, tiered_cheap, tiered_expensive])

    # Median of the tiered candidates only (20_000, 40_000) — the noname's
    # rock-bottom price must not drag the reference down.
    assert market.trusted_median_price == 30_000
    assert market.median_price == 20_000  # pool median unaffected, still available


def test_trusted_median_price_none_when_no_tiers():
    a = _candidate(source_product_id="1", price=10_000, brand_tier=None)
    b = _candidate(source_product_id="2", price=20_000, brand_tier=None)

    market = compute_market_context([a, b])

    assert market.trusted_median_price is None
    assert market.median_price == 15_000


def test_cheapest_noname_no_longer_auto_wins_price_axis():
    """The old bug: cheapest-in-pool got full price credit regardless of
    whether it was a trustworthy price or just an unknown-brand outlier."""
    noname = _candidate(source_product_id="1", price=4_184, brand="Noname", brand_tier=None)
    trusted_mid = _candidate(source_product_id="2", price=20_000, brand_tier="B")
    trusted_high = _candidate(source_product_id="3", price=40_000, brand_tier="A")
    market = compute_market_context([noname, trusted_mid, trusted_high])
    constraints = _constraints(required_features=[])

    noname_scored = score(noname, constraints, market)
    trusted_scored = score(trusted_mid, constraints, market)

    assert trusted_scored.total_score > noname_scored.total_score


def test_price_axis_full_credit_at_or_below_60_percent_of_reference():
    # Three tiered prices [18_000, 30_000, 30_000] → trusted median = 30_000.
    reference_setter_1 = _candidate(source_product_id="1", price=30_000, brand_tier="A")
    reference_setter_2 = _candidate(source_product_id="2", price=30_000, brand_tier="B")
    at_threshold = _candidate(source_product_id="3", price=18_000, brand_tier="A")  # 60% of 30k
    market = compute_market_context([reference_setter_1, reference_setter_2, at_threshold])
    assert market.trusted_median_price == 30_000
    constraints = _constraints(required_features=[])

    axis = next(a for a in score(at_threshold, constraints, market).axes if a.name == "price")
    assert axis.score == axis.max_score


def test_suspicious_price_penalty_and_con_for_far_below_median_noname():
    trusted_a = _candidate(source_product_id="1", price=30_000, brand_tier="A")
    trusted_b = _candidate(source_product_id="2", price=30_000, brand_tier="B")
    noname_cheap = _candidate(source_product_id="3", price=4_184, brand="Noname", brand_tier=None)
    market = compute_market_context([trusted_a, trusted_b, noname_cheap])
    constraints = _constraints(required_features=[])

    assert market.trusted_median_price == 30_000
    scored = score(noname_cheap, constraints, market)

    assert "подозрительно низкая цена для категории" in scored.cons
    price_axis = next(a for a in scored.axes if a.name == "price")
    assert "подозрительно низкая цена" in price_axis.explanation


def test_suspicious_price_falls_back_to_pool_median_without_tiered_brands():
    pool = [
        _candidate(source_product_id=str(i), price=p, brand=None, brand_tier=None)
        for i, p in enumerate([16_000, 20_000, 22_000, 25_000])
    ]
    toy = _candidate(source_product_id="toy", price=3_255, brand=None, brand_tier=None)
    market = compute_market_context([*pool, toy])

    assert market.trusted_median_price is None
    assert "подозрительно низкая цена для категории" in score(toy, _constraints(), market).cons
    assert "подозрительно низкая цена для категории" not in (
        score(pool[0], _constraints(), market).cons
    )


def test_no_suspicious_penalty_when_price_near_trusted_median():
    trusted_a = _candidate(source_product_id="1", price=30_000, brand_tier="A")
    noname_near_median = _candidate(
        source_product_id="2", price=28_000, brand="Noname", brand_tier=None
    )
    market = compute_market_context([trusted_a, noname_near_median])
    constraints = _constraints(required_features=[])

    scored = score(noname_near_median, constraints, market)

    assert "подозрительно низкая цена для категории" not in scored.cons


# --- best_premium role -------------------------------------------------------


def test_best_premium_never_assigned_to_unknown_brand():
    noname_expensive = _candidate(
        source_product_id="1",
        price=13_990,
        brand="KERN",
        brand_tier=None,
        rating=4.8,
        review_count=1_000,
    )
    tiered = _candidate(source_product_id="2", price=10_000, brand_tier="A", rating=4.0)

    recommendation = rank([noname_expensive, tiered], _constraints(required_features=[]))

    premium = recommendation.alternatives.get("best_premium")
    assert premium is None or premium.candidate.brand_tier in ("A", "B")
    assert not (premium is not None and premium.candidate.source_product_id == "1")


# --- review credibility -----------------------------------------------------


def test_unknown_brand_reviews_axis_discounted_vs_tiered_candidate():
    tiered = _candidate(source_product_id="1", brand_tier="C", rating=4.9, review_count=200)
    unknown = _candidate(
        source_product_id="2", brand="HAROOKO", brand_tier=None, rating=4.9, review_count=200
    )
    market = compute_market_context([tiered, unknown])
    constraints = _constraints(required_features=[])

    tiered_reviews = next(a for a in score(tiered, constraints, market).axes if a.name == "reviews")
    unknown_reviews = next(
        a for a in score(unknown, constraints, market).axes if a.name == "reviews"
    )

    assert unknown_reviews.score < tiered_reviews.score
    assert "discounted: unverified brand" in unknown_reviews.explanation


def test_unknown_brand_popularity_axis_discounted_vs_tiered_candidate():
    tiered = _candidate(source_product_id="1", brand_tier="C", review_count=200)
    unknown = _candidate(source_product_id="2", brand="HAROOKO", brand_tier=None, review_count=200)
    market = compute_market_context([tiered, unknown])
    constraints = _constraints(required_features=[])

    tiered_pop = next(a for a in score(tiered, constraints, market).axes if a.name == "popularity")
    unknown_pop = next(
        a for a in score(unknown, constraints, market).axes if a.name == "popularity"
    )

    assert unknown_pop.score < tiered_pop.score
    assert "discounted: unverified brand" in unknown_pop.explanation


def test_popularity_normalized_per_source_not_globally():
    """Ozon's RU-wide review counts must not dwarf Kaspi's local counts: each
    candidate is compared only to the busiest listing from its own source, so a
    Kaspi listing at its source max scores as high as an Ozon one at its."""
    ozon = _candidate(source="ozon", source_product_id="1", brand_tier="A", review_count=90_000)
    kaspi = _candidate(source="kaspi", source_product_id="2", brand_tier="A", review_count=200)
    market = compute_market_context([ozon, kaspi])
    constraints = _constraints(required_features=[])

    ozon_pop = next(a for a in score(ozon, constraints, market).axes if a.name == "popularity")
    kaspi_pop = next(a for a in score(kaspi, constraints, market).axes if a.name == "popularity")

    # Kaspi (200 reviews) is 100% of its own pool max, exactly like Ozon (90k):
    # the 200-review listing is NOT crushed by the 90k one on a shared axis.
    assert kaspi_pop.score == ozon_pop.score
    assert kaspi_pop.score == DEFAULT_WEIGHTS["popularity"]
    assert "kaspi pool max (200)" in kaspi_pop.explanation


def test_reviews_axis_normalized_per_source_not_globally():
    """Problem A: the reviews-axis volume is log-capped against the candidate's
    own source max, mirroring popularity — Ozon's 90k RU-wide count must not
    crush a Kaspi listing with 200 local reviews on a shared axis."""
    ozon = _candidate(
        source="ozon", source_product_id="1", brand_tier="A", rating=4.5, review_count=90_000
    )
    kaspi = _candidate(
        source="kaspi", source_product_id="2", brand_tier="A", rating=4.5, review_count=200
    )
    market = compute_market_context([ozon, kaspi])
    constraints = _constraints(required_features=[])

    ozon_reviews = next(a for a in score(ozon, constraints, market).axes if a.name == "reviews")
    kaspi_reviews = next(a for a in score(kaspi, constraints, market).axes if a.name == "reviews")

    # Each is at its own source max → both get full volume credit, equal score.
    assert kaspi_reviews.score == ozon_reviews.score
    assert kaspi_reviews.score > 0


def test_apply_review_trust_discounts_reviews_axis_and_total():
    tier_a = _candidate(brand_tier="A", rating=5.0, review_count=200)
    market = compute_market_context([tier_a])
    scored = score(tier_a, _constraints(required_features=[]), market)
    reviews_before = next(a for a in scored.axes if a.name == "reviews").score

    adjusted = apply_review_trust(scored, 0.5, "98% five-star")

    reviews_after = next(a for a in adjusted.axes if a.name == "reviews")
    assert reviews_after.score == round(reviews_before * 0.5, 2)
    assert adjusted.total_score < scored.total_score
    assert "review trust 0.5: 98% five-star" in reviews_after.explanation


def test_apply_review_trust_composes_with_credibility_via_min():
    # Unknown brand: credibility 0.4 already discounts harder than trust 0.5,
    # so min() keeps the tighter 0.4 and the reviews score is unchanged.
    unknown = _candidate(brand="Noname", brand_tier=None, rating=5.0, review_count=200)
    market = compute_market_context([unknown])
    scored = score(unknown, _constraints(required_features=[]), market)
    reviews_before = next(a for a in scored.axes if a.name == "reviews").score

    adjusted = apply_review_trust(scored, 0.5, "98% five-star")

    assert next(a for a in adjusted.axes if a.name == "reviews").score == reviews_before


def test_suspicious_price_candidate_excluded_from_all_alternative_roles():
    """Problem B: a candidate flagged with the suspicious-price con stays in the
    ranked list but is barred from every alternative role."""
    trusted_a = _candidate(source_product_id="1", price=30_000, brand_tier="A")
    trusted_b = _candidate(source_product_id="2", price=25_000, brand_tier="B")
    trusted_c = _candidate(source_product_id="3", price=28_000, brand_tier="A")
    noname_cheap = _candidate(source_product_id="4", price=4_184, brand="Noname", brand_tier=None)

    recommendation = rank(
        [trusted_a, trusted_b, trusted_c, noname_cheap], _constraints(required_features=[])
    )

    ranked_ids = {s.candidate.source_product_id for s in recommendation.ranked}
    assert "4" in ranked_ids  # still visible in the ranked list
    alt_ids = {a.candidate.source_product_id for a in recommendation.alternatives.values()}
    assert "4" not in alt_ids  # but never endorsed as a "best X"


def test_tier_a_brand_reviews_not_discounted():
    tier_a = _candidate(brand_tier="A", rating=4.9, review_count=200)
    market = compute_market_context([tier_a])
    constraints = _constraints(required_features=[])

    reviews_axis = next(a for a in score(tier_a, constraints, market).axes if a.name == "reviews")

    assert "discounted" not in reviews_axis.explanation


def test_best_premium_omitted_when_no_tiered_candidates_present():
    noname_a = _candidate(source_product_id="1", price=10_000, brand_tier=None)
    noname_b = _candidate(source_product_id="2", price=20_000, brand_tier=None)

    recommendation = rank([noname_a, noname_b], _constraints(required_features=[]))

    assert "best_premium" not in recommendation.alternatives


# --- alternatives: dedupe against the best pick and each other -------------


def test_pool_of_one_produces_no_alternatives_duplicating_best():
    """Bug A: with a single passing candidate, 'best value' must not be the
    same product as the best pick — the role is dropped, not duplicated."""
    only = _candidate(source_product_id="1", price=30_000)

    recommendation = rank([only], _constraints(required_features=[]))

    assert recommendation.alternatives == {}
    best = recommendation.best.candidate
    best_id = (best.source, best.source_product_id)
    for alt in recommendation.alternatives.values():
        assert (alt.candidate.source, alt.candidate.source_product_id) != best_id


def test_alternatives_never_repeat_the_best_pick_or_each_other():
    def _scored(pid: str, total: float, price: int, tier: str | None = None) -> ScoredCandidate:
        cand = _candidate(source_product_id=pid, price=price, brand_tier=tier)
        axis = AxisScore(name="total", score=total, max_score=100.0, explanation="x")
        return ScoredCandidate(candidate=cand, total_score=total, confidence=1.0, axes=[axis])

    best = _scored("1", 90.0, 30_000, tier="A")
    only_other = _scored("2", 80.0, 20_000, tier="A")

    # With only one candidate besides the best pick, every role that gets
    # filled must resolve to that same single product — never back to best.
    alternatives = _pick_alternatives(best, [only_other], [best, only_other])

    for alt in alternatives.values():
        assert alt.candidate.source_product_id == "2"
    # At most one distinct product can be claimed across all roles here.
    assert len({alt.candidate.source_product_id for alt in alternatives.values()}) <= 1


# --- best_value gating: score threshold, not raw score/price ratio ---------


def test_best_value_excludes_low_score_junk():
    """Bug B: a suspiciously-cheap, low-scoring product must not win
    best_value purely by having the best raw score/price ratio."""

    def _scored(pid: str, total: float, price: int) -> ScoredCandidate:
        cand = _candidate(source_product_id=pid, price=price)
        axis = AxisScore(name="total", score=total, max_score=100.0, explanation="x")
        return ScoredCandidate(candidate=cand, total_score=total, confidence=1.0, axes=[axis])

    best = _scored("1", 74.0, 30_000)
    junk = _scored("2", 43.0, 4_184)  # best score/price ratio, but far below best's score
    decent = _scored("3", 68.0, 25_000)  # within 80% of best, legitimate value pick

    alternatives = _pick_alternatives(best, [junk, decent], [best, junk, decent])

    assert alternatives["best_value"].candidate.source_product_id == "3"


# --- dup-price-gap suspicion (same-product listing found much cheaper) -----


def test_dup_price_gap_gets_price_penalty_specific_con_and_role_bar():
    trusted_a = _candidate(source_product_id="1", price=30_000, brand_tier="A")
    trusted_b = _candidate(source_product_id="2", price=25_000, brand_tier="B")
    dup_gap = _candidate(source_product_id="3", price=3_208, brand_tier="B", dup_price_gap=3.5)
    market = compute_market_context([trusted_a, trusted_b, dup_gap])
    constraints = _constraints(required_features=[])

    scored = score(dup_gap, constraints, market)

    price_axis = next(a for a in scored.axes if a.name == "price")
    # M7: the dup-gap case gets the specific gap wording in the price-axis
    # explanation too, not the generic unknown-brand-vs-median phrase.
    assert "3.5 раза ниже другого листинга" in price_axis.explanation
    assert "подозрительно низкая цена для категории" not in price_axis.explanation
    assert any("3.5 раза ниже другого листинга" in c for c in scored.cons)
    assert SUSPICIOUS_PRICE_CON not in scored.cons  # specific con, not the generic one

    recommendation = rank([trusted_a, trusted_b, dup_gap], _constraints(required_features=[]))
    alt_ids = {a.candidate.source_product_id for a in recommendation.alternatives.values()}
    assert "3" not in alt_ids


def test_dup_price_gap_above_market_reference_is_not_suspicious():
    # Regression (live run): a Samsung at 23,158 ₸ (ABOVE the trusted median)
    # merged with an overpriced 62k duplicate must not eat the counterfeit
    # penalty — the pricier listing is the outlier, not this one.
    cheap_b = _candidate(source_product_id="1", price=10_000, brand_tier="B")
    mid_b = _candidate(source_product_id="2", price=14_000, brand_tier="B")
    above_ref = _candidate(source_product_id="3", price=23_000, brand_tier="A", dup_price_gap=2.7)
    market = compute_market_context([cheap_b, mid_b, above_ref])
    assert above_ref.price > (market.trusted_median_price or 0)  # premise of the test
    constraints = _constraints(required_features=[])

    scored = score(above_ref, constraints, market)

    price_axis = next(a for a in scored.axes if a.name == "price")
    assert "ниже другого листинга" not in price_axis.explanation
    assert not any("ниже другого листинга" in c for c in scored.cons)
    recommendation = rank([cheap_b, mid_b, above_ref], constraints)
    # Not barred: it may hold roles (here best_premium as the priciest A/B).
    assert recommendation.alternatives, "above-reference dup-gap candidate must stay eligible"


def test_no_dup_price_gap_leaves_price_axis_and_cons_unchanged():
    trusted_a = _candidate(source_product_id="1", price=30_000, brand_tier="A")
    plain = _candidate(source_product_id="2", price=28_000, brand_tier="B", dup_price_gap=None)
    market = compute_market_context([trusted_a, plain])
    constraints = _constraints(required_features=[])

    scored = score(plain, constraints, market)

    assert not any("раза ниже другого листинга" in c for c in scored.cons)


# --- brand-mimicry (counterfeit) candidates ---------------------------------


def test_mimic_brand_gets_counterfeit_con_and_never_wins_alternative_roles():
    trusted = _candidate(source_product_id="1", price=30_000, brand_tier="A", rating=4.5)
    fake = _candidate(
        source_product_id="2",
        title="Наушники беспроводные J BL Tws300",
        brand=None,
        brand_tier=None,
        price=2_000,  # cheapest by far — would otherwise auto-win best_budget
        rating=3.0,
        review_count=10,
        mimic_brand="JBL",
    )
    constraints = _constraints(required_features=[])

    scored = score(fake, constraints, market=compute_market_context([trusted, fake]))
    assert "возможная подделка под JBL" in scored.cons

    recommendation = rank([trusted, fake], constraints)
    alt_ids = {a.candidate.source_product_id for a in recommendation.alternatives.values()}
    assert "2" not in alt_ids
    assert recommendation.best.candidate.source_product_id == "1"


def test_mimic_brand_also_gets_price_axis_penalty():
    """HIGH-2a: a counterfeit's only edge is usually its rock-bottom price —
    the mimic-brand signal must also drive the suspicious-price penalty on
    the price axis itself, not just add a con. Price is kept near the trusted
    median (not suspiciously low by ratio) so the *generic* unknown-brand
    threshold stays clean — isolating the mimic-only trigger."""
    trusted_a = _candidate(source_product_id="1", price=30_000, brand_tier="A")
    trusted_b = _candidate(source_product_id="2", price=25_000, brand_tier="B")
    plain_unknown = _candidate(source_product_id="3", price=27_000, brand=None, brand_tier=None)
    fake = _candidate(
        source_product_id="4",
        price=27_000,
        brand=None,
        brand_tier=None,
        mimic_brand="JBL",
    )
    market = compute_market_context([trusted_a, trusted_b, plain_unknown])
    constraints = _constraints(required_features=[])

    clean_scored = score(plain_unknown, constraints, market)
    clean_price_axis = next(a for a in clean_scored.axes if a.name == "price")
    # Sanity: not generically suspicious (isolates the mimic-only trigger).
    assert "подозрительно низкая цена" not in clean_price_axis.explanation

    scored = score(fake, constraints, market)
    fake_price_axis = next(a for a in scored.axes if a.name == "price")

    assert fake_price_axis.score < clean_price_axis.score
    assert "возможная подделка под JBL" in fake_price_axis.explanation


def test_mimic_candidate_never_becomes_best_even_if_top_scoring():
    """HIGH-2b: when a mimic-brand candidate would otherwise be the raw
    top-scorer (e.g. hard filters already excluded everything but junk),
    ``assemble_recommendation`` must prefer the highest-scoring non-barred
    candidate as ``best`` instead."""

    def _scored(pid: str, total: float, mimic_brand: str | None) -> ScoredCandidate:
        cand = _candidate(source_product_id=pid, mimic_brand=mimic_brand)
        axis = AxisScore(name="total", score=total, max_score=100.0, explanation="x")
        return ScoredCandidate(candidate=cand, total_score=total, confidence=1.0, axes=[axis])

    fake_top = _scored("1", 95.0, "JBL")  # highest raw score, but barred
    runner_up = _scored("2", 60.0, None)

    recommendation = assemble_recommendation([fake_top, runner_up], rejected_count=0)

    assert recommendation.best.candidate.source_product_id == "2"


def test_best_falls_back_to_top_score_when_every_candidate_is_barred():
    """HIGH-2b fallback: nothing trustworthy in the pool → best is just the
    raw top score, since there's no non-barred candidate to prefer."""

    def _scored(pid: str, total: float, mimic_brand: str | None) -> ScoredCandidate:
        cand = _candidate(source_product_id=pid, mimic_brand=mimic_brand)
        axis = AxisScore(name="total", score=total, max_score=100.0, explanation="x")
        return ScoredCandidate(candidate=cand, total_score=total, confidence=1.0, axes=[axis])

    only_a = _scored("1", 80.0, "JBL")
    only_b = _scored("2", 50.0, "Xiaomi")

    recommendation = assemble_recommendation([only_a, only_b], rejected_count=0)

    assert recommendation.best.candidate.source_product_id == "1"  # raw top score


def test_review_counterfeit_con_bars_candidate_from_all_roles():
    """M6: a listing whose review-summary verdict flags a counterfeit must
    also be barred from best/alternative roles after the re-assemble, same
    as the price-based signals."""
    trusted = _scored(candidate=_candidate(source_product_id="1"), total_score=90.0)
    flagged = apply_summary_penalty(
        _scored(candidate=_candidate(source_product_id="2", price=1_000), total_score=95.0),
        ReviewSummary(counterfeit_suspicion=True),
    )
    assert COUNTERFEIT_CON in flagged.cons  # sanity: penalty applied the con

    recommendation = assemble_recommendation([trusted, flagged], rejected_count=0)

    assert recommendation.best.candidate.source_product_id == "1"
    alt_ids = {a.candidate.source_product_id for a in recommendation.alternatives.values()}
    assert "2" not in alt_ids


# --- best_budget quality floor ----------------------------------------------


def test_best_budget_floor_skips_low_scoring_cheapest_candidate():
    def _scored(pid: str, total: float, price: int) -> ScoredCandidate:
        cand = _candidate(source_product_id=pid, price=price)
        axis = AxisScore(name="total", score=total, max_score=100.0, explanation="x")
        return ScoredCandidate(candidate=cand, total_score=total, confidence=1.0, axes=[axis])

    best = _scored("1", 90.0, 30_000)
    junk = _scored("2", 30.0, 2_000)  # far below 0.5 * best.total_score, cheapest
    decent = _scored("3", 60.0, 20_000)  # qualifies, next cheapest

    alternatives = _pick_alternatives(best, [junk, decent], [best, junk, decent])

    assert alternatives["best_budget"].candidate.source_product_id == "3"


def test_best_budget_role_dropped_when_no_candidate_meets_floor():
    def _scored(pid: str, total: float, price: int) -> ScoredCandidate:
        cand = _candidate(source_product_id=pid, price=price)
        axis = AxisScore(name="total", score=total, max_score=100.0, explanation="x")
        return ScoredCandidate(candidate=cand, total_score=total, confidence=1.0, axes=[axis])

    best = _scored("1", 90.0, 30_000)
    junk = _scored("2", 20.0, 2_000)

    alternatives = _pick_alternatives(best, [junk], [best, junk])

    assert "best_budget" not in alternatives


# --- required-feature matching robustness -----------------------------------


def test_feature_present_normalizes_hyphens_and_spaces():
    hyphenated = _candidate(specs={"waterproof": "IPX-7"})
    spaced = _candidate(specs={"waterproof": "IPX 7"})

    assert _feature_present("IPX7", hyphenated)
    assert _feature_present("IPX7", spaced)


def test_feature_present_waterproof_synonym_bridge():
    russian_synonym = _candidate(specs={"защита": "Влагозащита IPX 7"}, title="Xiaomi F300")
    no_digits_synonym = _candidate(
        specs={"защита": "водонепроницаемый корпус"}, title="Xiaomi F300"
    )
    english_synonym = _candidate(specs={}, title="Xiaomi F300 waterproof design")

    assert _feature_present("IPX7", russian_synonym)
    assert _feature_present("IPX7", no_digits_synonym)
    assert _feature_present("IPX7", english_synonym)


def test_empty_specs_candidate_kept_with_unverified_con():
    """Bug A root cause: specs not fetched (empty dict) must not be treated
    as "feature absent" — the candidate survives filtering with a con
    instead of being wrongly rejected."""
    unfetched = _candidate(source_product_id="1", specs={}, title="Xiaomi Mijia F300 irrigator")
    constraints = _constraints(required_features=["IPX7"])

    passed, rejected = apply_hard_filters([unfetched], constraints)
    assert passed == [unfetched]
    assert rejected == []

    market = compute_market_context([unfetched])
    scored = score(unfetched, constraints, market)
    assert "не удалось проверить: IPX7" in scored.cons


# --- degenerate pool-of-1 popularity axis ------------------------------------


def test_pool_of_one_popularity_axis_is_neutral():
    """Bug C: a lone candidate's review count is 100% of a pool max of
    itself — that must read as neutral, not "strong popularity"."""
    only = _candidate(source_product_id="1", review_count=54)
    market = compute_market_context([only])

    axis = next(
        a
        for a in score(only, _constraints(required_features=[]), market).axes
        if a.name == "popularity"
    )

    assert axis.explanation == "single candidate — no pool comparison"
    assert axis.score == round(axis.max_score * 0.5, 2)


# --- price-trend signal (30-day history) -------------------------------------


def _price_axis(scored: ScoredCandidate) -> AxisScore:
    return next(a for a in scored.axes if a.name == "price")


def test_price_below_history_average_adds_pro_and_bonus():
    """Current price under 0.93x the 30-day avg → pro + a small price-axis bonus."""
    hist = PriceHistory(avg=40_000, min=32_000, count=6)
    cand = _candidate(price=32_000, price_history=hist)
    market = compute_market_context([cand])
    constraints = _constraints(required_features=[])

    scored = score(cand, constraints, market)
    baseline = score(_candidate(price=32_000), constraints, market)

    assert "цена ниже средней" in scored.pros
    assert "средняя за 30д" in _price_axis(scored).explanation
    assert _price_axis(scored).score > _price_axis(baseline).score
    assert _price_axis(scored).score <= _price_axis(scored).max_score


def test_price_above_history_average_adds_con_and_penalty():
    """Current price above 1.07x the 30-day avg → con + a small price-axis penalty."""
    hist = PriceHistory(avg=20_000, min=18_000, count=6)
    cand = _candidate(price=30_000, price_history=hist)
    market = compute_market_context([cand])
    constraints = _constraints(required_features=[])

    scored = score(cand, constraints, market)
    baseline = score(_candidate(price=30_000), constraints, market)

    assert "цена выше средней" in scored.cons
    assert "средняя за 30д" in _price_axis(scored).explanation
    assert _price_axis(scored).score < _price_axis(baseline).score


def test_price_near_history_average_is_neutral():
    """Within the +-7% dead band → no pro/con, no explanation change."""
    hist = PriceHistory(avg=35_000, min=34_000, count=4)
    cand = _candidate(price=35_000, price_history=hist)
    market = compute_market_context([cand])
    constraints = _constraints(required_features=[])

    scored = score(cand, constraints, market)

    assert "цена ниже средней" not in scored.pros
    assert "цена выше средней" not in scored.cons


def test_no_history_leaves_price_axis_unchanged():
    """No price_history → identical scoring to before the trend signal existed."""
    market = compute_market_context([_candidate()])
    constraints = _constraints(required_features=[])

    with_none = score(_candidate(price_history=None), constraints, market)

    assert "средняя за 30д" not in _price_axis(with_none).explanation
    assert "цена ниже средней" not in with_none.pros
    assert "цена выше средней" not in with_none.cons


# --- review-summary penalty ----------------------------------------------


from buysmarter.llm.review_summary import ReviewSummary  # noqa: E402
from buysmarter.ranking.penalties import apply_summary_penalty  # noqa: E402


def _scored(**overrides) -> ScoredCandidate:
    defaults = dict(
        candidate=_candidate(),
        total_score=90.0,
        confidence=1.0,
        axes=[],
        pros=[],
        cons=[],
    )
    defaults.update(overrides)
    return ScoredCandidate(**defaults)


def test_penalty_counterfeit_scales_score_and_confidence_by_0_6():
    scored = _scored(total_score=67.0, confidence=1.0)
    summary = ReviewSummary(counterfeit_suspicion=True)

    out = apply_summary_penalty(scored, summary)

    assert out.total_score == round(67.0 * 0.6, 2)
    assert out.confidence == round(1.0 * 0.6, 2)
    assert out.review_penalty == round(67.0 - 67.0 * 0.6, 2)
    assert "отзывы подозревают подделку" in out.cons


def test_penalty_serious_reliability_scales_by_0_85_with_red_flag_con():
    scored = _scored(total_score=90.0, confidence=1.0)
    summary = ReviewSummary(reliability_concern="serious", red_flags=["ломается за полгода"])

    out = apply_summary_penalty(scored, summary)

    assert out.total_score == round(90.0 * 0.85, 2)
    assert out.confidence == round(1.0 * 0.85, 2)
    assert "ломается за полгода" in out.cons


def test_penalty_minor_reliability_adds_con_only():
    scored = _scored(total_score=90.0, confidence=1.0)
    summary = ReviewSummary(reliability_concern="minor", red_flags=["иногда протекает"])

    out = apply_summary_penalty(scored, summary)

    assert out.total_score == 90.0
    assert out.confidence == 1.0
    assert out.review_penalty == 0.0
    assert "иногда протекает" in out.cons


def test_penalty_composes_counterfeit_and_serious():
    scored = _scored(total_score=100.0, confidence=1.0)
    summary = ReviewSummary(counterfeit_suspicion=True, reliability_concern="serious")

    out = apply_summary_penalty(scored, summary)

    assert out.total_score == round(100.0 * 0.6 * 0.85, 2)
    assert out.confidence == round(1.0 * 0.6 * 0.85, 2)


def test_penalty_noop_when_clean_returns_same_object():
    scored = _scored()
    out = apply_summary_penalty(scored, ReviewSummary())
    assert out is scored


def test_penalty_counterfeit_softer_on_multi_seller_kaspi_card():
    """Multi-seller card → softer 0.8 factor + seller-aware con, not a blanket fake."""
    single = _scored(total_score=100.0, confidence=1.0)  # offer_count default 0
    multi = _scored(
        total_score=100.0,
        confidence=1.0,
        candidate=_candidate(offer_count=12),
    )
    summary = ReviewSummary(counterfeit_suspicion=True)

    single_out = apply_summary_penalty(single, summary)
    multi_out = apply_summary_penalty(multi, summary)

    assert single_out.total_score == 60.0  # 1-2 sellers → full ×0.6
    assert multi_out.total_score == 80.0  # >3 sellers → soft ×0.8
    assert "подозревают подделку" in single_out.cons[0]
    assert "у части продавцов" in multi_out.cons[0]
    assert single_out.confidence == 0.6
    assert multi_out.confidence == 0.8


# --- Fix 2a: degenerate-trusted-median honesty ------------------------------


def test_degenerate_trusted_median_flags_explanation_and_caps_confidence():
    # Only 2 tiered candidates -> trusted_median_price is set but shaky.
    trusted_a = _candidate(source_product_id="1", price=30_000, brand_tier="A")
    trusted_b = _candidate(source_product_id="2", price=28_000, brand_tier="B")
    market = compute_market_context([trusted_a, trusted_b])
    constraints = _constraints(required_features=[])

    assert market.tiered_pool_size == 2

    scored = score(trusted_a, constraints, market)
    price_axis = next(a for a in scored.axes if a.name == "price")

    assert "медиана по 2 товарам с известным брендом — ненадёжна" in price_axis.explanation
    assert scored.confidence <= 0.7


def test_non_degenerate_trusted_median_no_warning_no_cap():
    trusted = [_candidate(source_product_id=str(i), price=30_000, brand_tier="A") for i in range(3)]
    market = compute_market_context(trusted)
    constraints = _constraints(required_features=[])

    assert market.tiered_pool_size == 3

    scored = score(trusted[0], constraints, market)
    price_axis = next(a for a in scored.axes if a.name == "price")

    assert "ненадёжна" not in price_axis.explanation
    assert scored.confidence == 1.0  # unaffected: full data + non-degenerate market


def test_zero_tiered_candidates_also_flags_explanation_and_caps_confidence():
    """M3: zero tiered candidates silently fell back to the pool median with
    no honesty note and full confidence — must now ALSO flag + cap."""
    untiered = [_candidate(source_product_id=str(i), brand_tier=None) for i in range(3)]
    market = compute_market_context(untiered)
    constraints = _constraints(required_features=[])

    assert market.tiered_pool_size == 0
    assert market.trusted_median_price is None  # silently falls back to pool median

    scored = score(untiered[0], constraints, market)
    price_axis = next(a for a in scored.axes if a.name == "price")

    assert "нет товаров с известным брендом — референс по всему пулу" in price_axis.explanation
    assert scored.confidence <= 0.7


# --- Fix 3b: rejected_reasons breakdown --------------------------------------


def test_rank_populates_rejected_reasons_breakdown():
    cheap = _candidate(source_product_id="1", price=10_000)
    over_budget_a = _candidate(source_product_id="2", price=50_000)
    over_budget_b = _candidate(source_product_id="3", price=60_000)
    unavailable = _candidate(source_product_id="4", availability=False)

    recommendation = rank(
        [cheap, over_budget_a, over_budget_b, unavailable],
        _constraints(budget_max=40_000, required_features=[]),
    )

    assert recommendation.rejected_count == 3
    assert recommendation.rejected_reasons == {"price exceeds budget": 2, "not available": 1}


def test_assemble_recommendation_default_rejected_reasons_empty():
    scored = [
        ScoredCandidate(
            candidate=_candidate(),
            total_score=90.0,
            confidence=1.0,
            axes=[AxisScore(name="total", score=90.0, max_score=100.0, explanation="x")],
        )
    ]
    recommendation = assemble_recommendation(scored, 0)
    assert recommendation.rejected_reasons == {}


# --- Fix 3a: public is_barred wrapper ---------------------------------------


def test_is_barred_matches_mimic_and_clean_candidates():
    clean = _candidate(source_product_id="1", brand_tier="A")
    mimic = _candidate(source_product_id="2", brand=None, brand_tier=None, mimic_brand="JBL")
    market = compute_market_context([clean, mimic])
    constraints = _constraints(required_features=[])

    clean_scored = score(clean, constraints, market)
    mimic_scored = score(mimic, constraints, market)

    assert is_barred(clean_scored) is False
    assert is_barred(mimic_scored) is True


def test_zero_price_candidate_never_crashes_or_wins_value_or_budget_roles():
    """A price-0 listing ("invalid price" on the price axis) close to the best
    score must not crash best_value's score/price key nor win best_budget."""

    def _scored(pid: str, price: int, total: float) -> ScoredCandidate:
        return ScoredCandidate(
            candidate=_candidate(source_product_id=pid, price=price),
            total_score=total,
            confidence=1.0,
            axes=[],
        )

    best = _scored("best", 30_000, 80.0)
    zero = _scored("zero", 0, 75.0)
    decent = _scored("decent", 25_000, 70.0)

    alternatives = _pick_alternatives(best, [zero, decent], [best, zero, decent])

    assert alternatives["best_value"].candidate.source_product_id == "decent"
    assert "best_budget" not in alternatives or (alternatives["best_budget"].candidate.price > 0)


def test_market_context_ignores_non_positive_prices():
    ctx = compute_market_context(
        [
            _candidate(source_product_id="1", price=0),
            _candidate(source_product_id="2", price=-5),
            _candidate(source_product_id="3", price=10_000),
            _candidate(source_product_id="4", price=30_000),
        ]
    )
    assert ctx.min_price == 10_000
    assert ctx.median_price == 20_000
    assert ctx.trusted_median_price == 20_000


def test_market_context_all_prices_zero_does_not_crash():
    ctx = compute_market_context([_candidate(price=0), _candidate(source_product_id="2", price=0)])
    assert ctx.min_price == 0
    assert ctx.median_price == 0


def test_zero_price_candidate_is_not_crowned_best():
    free = _scored(candidate=_candidate(source_product_id="1", price=0), total_score=99.0)
    normal = _scored(candidate=_candidate(source_product_id="2", price=20_000), total_score=60.0)
    rec = assemble_recommendation([free, normal], rejected_count=0)
    assert rec.best.candidate.source_product_id == "2"


def test_sole_zero_price_candidate_still_becomes_best_without_crash():
    free = _scored(candidate=_candidate(price=0))
    rec = assemble_recommendation([free], rejected_count=0)
    assert rec.best is free


def test_negative_review_count_scores_are_finite_and_non_negative():
    cands = [
        _candidate(source_product_id="1", review_count=-1),
        _candidate(source_product_id="2", review_count=100),
    ]
    market = compute_market_context(cands)
    result = score(cands[0], _constraints(), market)
    for axis in result.axes:
        if axis.name in ("reviews", "popularity"):
            assert math.isfinite(axis.score)
            assert axis.score >= 0


# --- flavor/color variants of the same product -------------------------------

from buysmarter.ranking.selection import variant_key  # noqa: E402


def test_variant_key_equal_for_flavor_variants_of_same_product():
    a = "Протеин YummyLab PhaseX сывороточный 930 г шоколад"
    b = "Протеин YummyLab PhaseX сывороточный 930 г, вкус банан-клубника"

    assert variant_key(a) == variant_key(b)


def test_variant_key_equal_for_color_variants():
    assert variant_key("Шейкер BlenderBottle Classic 600 мл черный") == variant_key(
        "Шейкер BlenderBottle Classic 600 мл, цвет: Pink"
    )


def test_variant_key_differs_for_different_models_and_sizes():
    assert variant_key("YummyLab PhaseX 930 г шоколад") != variant_key(
        "YummyLab PhaseX 2 кг шоколад"
    )
    assert variant_key("Optimum Nutrition Gold Standard ваниль") != variant_key(
        "YummyLab PhaseX ваниль"
    )


def test_alternatives_skip_flavor_variant_of_best_pick():
    def _s(pid: str, title: str, total: float, price: int) -> ScoredCandidate:
        cand = _candidate(source_product_id=pid, title=title, price=price)
        return ScoredCandidate(candidate=cand, total_score=total, confidence=1.0, axes=[])

    best = _s("1", "YummyLab PhaseX 930 г шоколад", 59.0, 13_950)
    twin = _s("2", "YummyLab PhaseX 930 г банан-клубника", 59.0, 13_950)
    other = _s("3", "Optimum Nutrition Gold Standard 908 г ваниль", 55.0, 15_000)

    alternatives = _pick_alternatives(best, [twin, other], [best, twin, other])

    picked = {s.candidate.source_product_id for s in alternatives.values()}
    assert "2" not in picked
    assert alternatives["best_value"].candidate.source_product_id == "3"


def test_alternatives_skip_variant_of_already_assigned_role():
    def _s(pid: str, title: str, total: float, price: int) -> ScoredCandidate:
        cand = _candidate(source_product_id=pid, title=title, price=price)
        return ScoredCandidate(candidate=cand, total_score=total, confidence=1.0, axes=[])

    best = _s("1", "Brand A Whey 2 кг ваниль", 70.0, 30_000)
    value = _s("2", "Brand B Whey 1 кг шоколад", 68.0, 12_000)
    value_twin = _s("3", "Brand B Whey 1 кг клубника", 66.0, 11_900)

    alternatives = _pick_alternatives(best, [value, value_twin], [best, value, value_twin])

    assert alternatives["best_value"].candidate.source_product_id == "2"
    assert "best_budget" not in alternatives


def test_variant_key_milk_chocolate_matches_other_flavor() -> None:
    from buysmarter.ranking.selection import variant_key

    assert variant_key("Протеин YummyLab PhaseX молочный шоколад 950 г") == variant_key(
        "Протеин YummyLab PhaseX банан-клубника 950 г"
    )


# --- defect-complaint penalty ---------------------------------------------


from buysmarter.ranking.penalties import apply_defect_penalty  # noqa: E402


def test_defect_penalty_below_threshold_returns_same_object() -> None:
    scored = _scored(total_score=90.0)

    assert apply_defect_penalty(scored, complaints=1, sample=10) is scored
    assert apply_defect_penalty(scored, complaints=2, sample=20) is scored  # 10% < 20%


def test_defect_penalty_above_threshold_scales_total_and_adds_con() -> None:
    scored = _scored(total_score=90.0)

    out = apply_defect_penalty(scored, complaints=2, sample=10)  # 20% of sample

    assert out.total_score == round(90.0 * 0.9, 2)
    assert out.review_penalty == round(90.0 - 90.0 * 0.9, 2)
    assert "жалобы на брак в отзывах: 2 из 10" in out.cons


def test_defect_penalty_zero_sample_is_skipped() -> None:
    scored = _scored(total_score=90.0)

    assert apply_defect_penalty(scored, complaints=3, sample=0) is scored


# --- B5: requirement checks from review quotes ------------------------------

from buysmarter.llm.review_summary import RequirementCheck  # noqa: E402
from buysmarter.ranking.filters import feature_present  # noqa: E402
from buysmarter.ranking.penalties import apply_requirement_checks  # noqa: E402


def test_feature_present_is_public_alias_of_engine_helper() -> None:
    assert feature_present("IPX7", _candidate(title="Часы IPX-7")) is True
    assert feature_present("NFC", _candidate(title="Часы")) is False


def test_confirmed_check_swaps_unverified_con_for_pro() -> None:
    scored = _scored(cons=["не удалось проверить: шумоподавление", "другой минус"])
    check = RequirementCheck(
        feature="шумоподавление", verdict="confirmed", mentions=2, quote="шумит меньше"
    )
    out = apply_requirement_checks(scored, [check])
    assert out.cons == ["другой минус"]
    assert out.pros == ["отзывы подтверждают: шумоподавление — «шумит меньше»"]
    assert out.total_score == scored.total_score


def test_refuted_check_scales_score_and_records_penalty() -> None:
    scored = _scored(total_score=90.0)
    check = RequirementCheck(feature="IPX7", verdict="refuted", mentions=2, quote="вода внутрь")
    out = apply_requirement_checks(scored, [check])
    assert out.total_score == 72.0  # 90 * 0.8
    assert out.review_penalty == 18.0
    assert out.cons == ["по отзывам нет: IPX7 — «вода внутрь»"]


def test_two_refuted_features_compose_factors() -> None:
    scored = _scored(total_score=100.0)
    checks = [
        RequirementCheck(feature="IPX7", verdict="refuted", mentions=2, quote="вода внутрь"),
        RequirementCheck(feature="NFC", verdict="refuted", mentions=2, quote="NFC не работает"),
    ]
    out = apply_requirement_checks(scored, checks)
    assert out.total_score == 64.0  # 100 * 0.8 * 0.8
    assert out.review_penalty == 36.0


def test_insufficient_or_empty_checks_return_scored_itself() -> None:
    scored = _scored()
    assert apply_requirement_checks(scored, []) is scored
    assert apply_requirement_checks(scored, [RequirementCheck(feature="IPX7")]) is scored


def test_review_velocity_is_shown_on_popularity_axis_without_changing_score() -> None:
    base = _candidate(review_count=50)
    fast = base.model_copy(update={"review_velocity": 7.0})
    market = compute_market_context([base, _candidate(review_count=100, source_product_id="2")])
    plain = next(a for a in score(base, _constraints(), market).axes if a.name == "popularity")
    shown = next(a for a in score(fast, _constraints(), market).axes if a.name == "popularity")
    assert shown.score == plain.score
    assert "+7 отзывов/нед" in shown.explanation


def test_concentration_feature_not_hard_filtered_when_substance_axis_active():
    """Live run 2026-10-13: «крем с адапаленом 0,1%» rejected 5 adapalene products
    as "missing required feature 0,1%" — concentration is graded on the
    ingredient axis (partial credit), not string-matched as a hard filter."""
    from tests.fakes import composition_hooks

    cand = _candidate(title="Клензит гель адапален", specs={"Состав": "адапален"}, price=5_000)
    constraints = _constraints(
        search_phrase="крем с адапаленом", required_features=["0,1%"], budget_max=10_000
    )

    passed, _ = apply_hard_filters([cand], constraints, composition_hooks(constraints))
    assert passed == [cand]

    passed, rejected = apply_hard_filters([cand], constraints)
    assert passed == [] and "0,1%" in rejected[0][1]


# --- Kimovil index in the specs axis -------------------------------------------


def _specs_axis(candidate: Candidate, constraints: QueryConstraints) -> AxisScore:
    scored = score(candidate, constraints, compute_market_context([candidate]))
    return next(a for a in scored.axes if a.name == "specs")


def test_the_benchmark_index_is_averaged_into_the_specs_axis():
    axis = _specs_axis(
        _candidate(benchmark_index=60.0, benchmark_label="Kimovil"),
        _constraints(required_features=[]),
    )
    # No required features: the neutral 0.5 averaged with the index.
    assert axis.score == round(DEFAULT_WEIGHTS["specs"] * (0.5 + 0.6) / 2, 2)
    assert "Kimovil 60/100" in axis.explanation


def test_specs_axis_is_neutral_without_required_features():
    """A spec sheet (Ozon ships one, Kaspi search does not) must not outrank its absence."""
    no_req = _constraints(required_features=[])
    with_specs = _specs_axis(_candidate(specs={"цвет": "белый"}), no_req)
    without = _specs_axis(_candidate(specs={}), no_req)
    assert with_specs.score == without.score == DEFAULT_WEIGHTS["specs"] * 0.5


def test_without_a_benchmark_index_the_specs_axis_is_unchanged():
    axis = _specs_axis(_candidate(), _constraints(required_features=[]))
    assert axis.score == DEFAULT_WEIGHTS["specs"] * 0.5
    assert "Kimovil" not in axis.explanation


def test_a_phone_missing_from_the_benchmark_is_no_better_than_the_weakest_known_one():
    found = _candidate(source_product_id="found", benchmark_index=30.0, benchmark_label="Kimovil")
    missing = _candidate(source_product_id="missing", benchmark_missing=True)
    market = compute_market_context([found, missing])
    no_req = _constraints(required_features=[])

    axes = {
        c.source_product_id: next(a for a in score(c, no_req, market).axes if a.name == "specs")
        for c in (found, missing)
    }

    assert axes["missing"].score == axes["found"].score
    assert "weakest" in axes["missing"].explanation


def test_a_pool_of_strong_phones_does_not_lift_an_unknown_one():
    flagship = _candidate(source_product_id="f", benchmark_index=85.0, benchmark_label="Kimovil")
    missing = _candidate(source_product_id="missing", benchmark_missing=True)
    market = compute_market_context([flagship, missing])

    scored = score(missing, _constraints(required_features=[]), market)

    axis = next(a for a in scored.axes if a.name == "specs")
    assert axis.score == DEFAULT_WEIGHTS["specs"] * 0.5


def test_a_phone_never_looked_up_keeps_the_neutral_specs_half():
    found = _candidate(source_product_id="found", benchmark_index=30.0, benchmark_label="Kimovil")
    unchecked = _candidate(source_product_id="unchecked")  # past the lookup cap
    market = compute_market_context([found, unchecked])

    scored = score(unchecked, _constraints(required_features=[]), market)

    axis = next(a for a in scored.axes if a.name == "specs")
    assert axis.score == DEFAULT_WEIGHTS["specs"] * 0.5


def test_variant_key_equal_for_beige_and_brown_guitar_variants():
    assert variant_key("Гитара акустическая Liewen Toun AR38 бежевый") == variant_key(
        "Гитара акустическая Liewen Toun AR38 коричневый"
    )


def test_distinct_variants_keeps_top_scored_per_variant():
    from buysmarter.ranking.selection import distinct_variants

    def _s(pid: str, title: str) -> ScoredCandidate:
        cand = _candidate(source_product_id=pid, title=title, price=25_000)
        return ScoredCandidate(candidate=cand, total_score=50.0, confidence=1.0, axes=[])

    beige = _s("1", "Гитара Liewen Toun AR38 бежевый")
    red = _s("2", "Гитара Liewen Toun AR38 красный")
    other = _s("3", "Гитара ARG JG-38C KIT")
    black = _s("4", "Гитара Liewen Toun AR38 черный")

    assert distinct_variants([beige, red, other, black], 2) == [beige, other]


def test_an_empty_pass_says_why_every_candidate_was_rejected() -> None:
    over = [_candidate(source_product_id=str(i), price=60_000) for i in range(2)]

    with pytest.raises(NoCandidatesPassed) as caught:
        rank(over, _constraints(budget_max=40_000))

    assert caught.value.rejected_reasons == {"price exceeds budget": 2}
    assert isinstance(caught.value, ValueError)  # API, worker and MCP still catch it as before
