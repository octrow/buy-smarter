"""Composition-driven ingredient axis: scoring, equivalence pros, spec-driven parity.

Pure ranking engine — no DB, no LLM, no network.
"""

from buysmarter.ranking.engine import (
    COMPOSITION_WEIGHTS,
    DEFAULT_WEIGHTS,
    compute_market_context,
    rank,
    score,
)
from buysmarter.ranking.ingredient_axis import build_ingredient_context
from buysmarter.ranking.models import Candidate, QueryConstraints


def _cand(**overrides) -> Candidate:
    defaults = dict(
        source="kaspi",
        source_product_id="1",
        title="Product",
        brand=None,
        brand_tier=None,
        price=10_000,
        rating=4.5,
        review_count=100,
        specs={"состав": "x"},
        availability=True,
    )
    defaults.update(overrides)
    return Candidate(**defaults)


def _constraints(**overrides) -> QueryConstraints:
    defaults = dict(category="крем с адапаленом", required_features=[])
    defaults.update(overrides)
    return QueryConstraints(**defaults)


def _axis(scored, name):
    return next(a for a in scored.axes if a.name == name)


# --- weight tables ---------------------------------------------------------


def test_composition_weights_sum_to_100() -> None:
    assert sum(COMPOSITION_WEIGHTS.values()) == 100.0


def test_default_weights_untouched() -> None:
    # Spec-driven categories must keep the exact original weights (no ingredient).
    assert DEFAULT_WEIGHTS == {
        "brand": 25.0,
        "price": 25.0,
        "specs": 20.0,
        "reviews": 12.0,
        "popularity": 8.0,
        "availability": 10.0,
    }


# --- axis scoring: present / concentration / absent ------------------------


def _score_one(candidate, constraints, required):
    market = compute_market_context([candidate])
    ctx = build_ingredient_context([candidate], required)
    return score(candidate, constraints, market, weights=COMPOSITION_WEIGHTS, ingredient_ctx=ctx)


def test_ingredient_present_with_concentration_full_credit() -> None:
    cand = _cand(detected_substances=["адапален"], concentration="0.1%")
    scored = _score_one(cand, _constraints(), ["адапален"])
    axis = _axis(scored, "ingredient")
    assert axis.score == COMPOSITION_WEIGHTS["ingredient"]  # full


def test_ingredient_present_unknown_concentration_partial() -> None:
    cand = _cand(detected_substances=["адапален"], concentration=None)
    scored = _score_one(cand, _constraints(), ["адапален"])
    axis = _axis(scored, "ingredient")
    assert axis.score == round(COMPOSITION_WEIGHTS["ingredient"] * 0.7, 2)


def test_ingredient_absent_zero() -> None:
    cand = _cand(detected_substances=[], concentration=None)
    scored = _score_one(cand, _constraints(), ["адапален"])
    axis = _axis(scored, "ingredient")
    assert axis.score == 0.0
    assert "отсутствует" in axis.explanation


def test_required_concentration_mismatch_partial() -> None:
    cand = _cand(detected_substances=["адапален"], concentration="0.3%")
    scored = _score_one(cand, _constraints(required_features=["0.1%"]), ["адапален"])
    axis = _axis(scored, "ingredient")
    assert axis.score == round(COMPOSITION_WEIGHTS["ingredient"] * 0.7, 2)


# --- equivalence pros (the Клензит vs Differin case) -----------------------


def _adapalene_pool() -> list[Candidate]:
    klenzit = _cand(
        source_product_id="klenzit",
        title="Клензит 0,1% 15г",
        price=5_808,
        rating=4.9,
        review_count=102,
        detected_substances=["адапален"],
        concentration="0.1%",
    )
    differin = _cand(
        source_product_id="differin",
        title="Differin гель 0.1%",
        price=22_500,
        rating=4.7,
        review_count=40,
        detected_substances=["адапален"],
        concentration="0.1%",
    )
    return [klenzit, differin]


def test_cheapest_generic_ranks_above_expensive_with_pros() -> None:
    pool = _adapalene_pool()
    rec = rank(pool, _constraints(), composition=True, required_substances=["адапален"])

    assert rec.best.candidate.source_product_id == "klenzit"
    assert any("дженерик-аналог" in p and "Differin" in p for p in rec.best.pros)

    differin = next(s for s in rec.ranked if s.candidate.source_product_id == "differin")
    equiv = [p for p in differin.pros if "аналог по действующему веществу" in p]
    assert equiv, differin.pros
    assert "Клензит" in equiv[0]
    assert "3.9 раз" in equiv[0]  # 22500 / 5808 ≈ 3.87


def test_single_member_group_has_no_equivalence_pro() -> None:
    cand = _cand(detected_substances=["адапален"], concentration="0.1%")
    ctx = build_ingredient_context([cand], ["адапален"])
    assert ctx.groups == {}


# --- spec-driven path is unchanged -----------------------------------------


def test_spec_driven_rank_has_no_ingredient_axis() -> None:
    cand = _cand(brand="Xiaomi", brand_tier="C", specs={"waterproof": "IPX7"})
    rec = rank([cand], _constraints(category="ирригатор"))
    axis_names = {a.name for a in rec.best.axes}
    assert "ingredient" not in axis_names
    assert axis_names == {"brand", "price", "specs", "reviews", "popularity", "availability"}
