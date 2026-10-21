"""A pricier copy of the pick elsewhere is no pro; a large gap is a warning con."""

from buysmarter.ranking.models import Candidate, Recommendation, ScoredCandidate
from buysmarter.services.matching import CrossSourceNote, apply_cross_notes


def _rec() -> Recommendation:
    cand = Candidate(source="ozon", source_product_id="o1", title="x", price=2695)
    best = ScoredCandidate(candidate=cand, total_score=80, confidence=0.8, axes=[])
    return Recommendation(best=best, alternatives={}, ranked=[best])


def test_a_mildly_pricier_copy_elsewhere_adds_no_pro() -> None:
    rec = _rec()
    apply_cross_notes(rec, {("ozon", "o1"): CrossSourceNote("также на kaspi за 3\xa0277 ₸", 1.2)})
    assert rec.best.pros == [] and rec.best.cons == []


def test_a_large_gap_stays_a_warning_con() -> None:
    rec = _rec()
    apply_cross_notes(rec, {("ozon", "o1"): CrossSourceNote("также на kaspi за 6\xa0000 ₸", 2.2)})
    assert rec.best.pros == []
    assert rec.best.cons == [
        "⚠ та же модель на kaspi за 6\xa0000 ₸ — разрыв цен 2.2×, проверьте листинг"
    ]
