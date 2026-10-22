"""Required-substance hard filter + unconfirmed-composition axis credit.

Pure ranking engine — no DB, no LLM, no network. Covers the composition-driven
rule that a product proven to lack the requested active substance is rejected,
while a product whose состав we simply couldn't read survives (honest
uncertainty), and that spec-driven ranking is untouched.
"""

from buysmarter.ranking.engine import COMPOSITION_WEIGHTS, compute_market_context, rank, score
from buysmarter.ranking.filters import NO_SUBSTANCE_REASON, apply_hard_filters
from buysmarter.ranking.ingredient_axis import build_ingredient_context
from buysmarter.ranking.models import Candidate, QueryConstraints


def _cand(pid: str, *, detected=None, specs=None, price=5000, checked=True) -> Candidate:
    return Candidate(
        source="kaspi",
        source_product_id=pid,
        title=f"product {pid}",
        price=price,
        rating=4.5,
        review_count=50,
        detected_substances=detected or [],
        specs=specs or {},
        ingredients_checked=checked,
    )


def _constraints() -> QueryConstraints:
    return QueryConstraints(category="крем с адапаленом")


def _axis(scored, name):
    return next(a for a in scored.axes if a.name == name)


# --- hard filter -----------------------------------------------------------


def test_proven_absent_rejected_when_checked() -> None:
    # Состав declared, required substance not detected → proven absence.
    absent = _cand("a", detected=[], specs={"Состав": "вода, глицерин"})
    passed, rejected = apply_hard_filters(
        [absent], _constraints(), required_substances=["адапален"]
    )
    assert passed == []
    assert len(rejected) == 1
    cand, reason = rejected[0]
    assert cand.source_product_id == "a"
    assert reason.startswith(NO_SUBSTANCE_REASON)
    assert "адапален" in reason


def test_present_candidate_passes() -> None:
    present = _cand("p", detected=["адапален"], specs={"Состав": "адапален 0.1%"})
    passed, rejected = apply_hard_filters(
        [present], _constraints(), required_substances=["адапален"]
    )
    assert [c.source_product_id for c in passed] == ["p"]
    assert rejected == []


def test_null_composition_passes_as_data_gap() -> None:
    # No состав text at all → unverifiable, must NOT be rejected.
    unknown = _cand("u", detected=[], specs={})
    passed, rejected = apply_hard_filters(
        [unknown], _constraints(), required_substances=["адапален"]
    )
    assert [c.source_product_id for c in passed] == ["u"]
    assert rejected == []


def test_unchecked_with_composition_is_kept() -> None:
    # Extraction did not run or gave no answer: absence is unknowable, keep it.
    absent = _cand("a", detected=[], specs={"Состав": "вода, глицерин"}, checked=False)
    passed, rejected = apply_hard_filters(
        [absent], _constraints(), required_substances=["адапален"]
    )
    assert [c.source_product_id for c in passed] == ["a"]
    assert rejected == []


def test_filter_decides_per_candidate() -> None:
    # One pool, two verdicts: only the checked candidate can be proven absent.
    checked = _cand("c", detected=[], specs={"Состав": "вода"})
    unchecked = _cand("u", detected=[], specs={"Состав": "вода"}, checked=False)
    passed, rejected = apply_hard_filters(
        [checked, unchecked], _constraints(), required_substances=["адапален"]
    )
    assert [c.source_product_id for c in passed] == ["u"]
    assert [c.source_product_id for c, _ in rejected] == ["c"]


def test_no_required_substances_never_rejects() -> None:
    absent = _cand("a", detected=[], specs={"Состав": "вода"})
    passed, _ = apply_hard_filters([absent], _constraints(), required_substances=[])
    assert [c.source_product_id for c in passed] == ["a"]


# --- unconfirmed-composition axis + con ------------------------------------


def _score_one(cand):
    market = compute_market_context([cand])
    ctx = build_ingredient_context([cand], ["адапален"])
    return score(cand, _constraints(), market, weights=COMPOSITION_WEIGHTS, ingredient_ctx=ctx)


def test_unchecked_composition_gets_partial_credit_and_con() -> None:
    unknown = _cand("u", detected=[], specs={"Состав": "вода"}, checked=False)
    scored = _score_one(unknown)
    axis = _axis(scored, "ingredient")
    assert axis.score == round(COMPOSITION_WEIGHTS["ingredient"] * 0.3, 2)
    assert "не проверен" in axis.explanation
    assert any("состав не проверен: адапален?" == c for c in scored.cons)
    assert "weak ingredient" not in scored.cons


def test_proven_absent_axis_is_zero() -> None:
    absent = _cand("a", detected=[], specs={"Состав": "вода"})
    scored = _score_one(absent)
    axis = _axis(scored, "ingredient")
    assert axis.score == 0.0
    assert "отсутствует" in axis.explanation


def test_rank_rejects_proven_absent_keeps_present() -> None:
    present = _cand("klenzit", detected=["адапален"], specs={"Состав": "адапален 0.1%"})
    absent = _cand("bioderma", detected=[], specs={"Состав": "вода, глицерин"})
    rec = rank(
        [present, absent],
        _constraints(),
        composition=True,
        required_substances=["адапален"],
    )
    assert rec.best.candidate.source_product_id == "klenzit"
    ranked_ids = {s.candidate.source_product_id for s in rec.ranked}
    assert "bioderma" not in ranked_ids
    assert rec.rejected_count == 1
