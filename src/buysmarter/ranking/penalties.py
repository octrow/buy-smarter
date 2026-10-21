"""Post-score adjustments from reviews.

Review trust, the counterfeit/reliability summary, defect complaints, requirement checks.
"""

from typing import TYPE_CHECKING

from buysmarter.ranking.engine import credibility_factor
from buysmarter.ranking.flags import COUNTERFEIT_CON, COUNTERFEIT_CON_MULTI, UNVERIFIED_FEATURE_CON
from buysmarter.ranking.models import (
    AxisScore,
    Candidate,
    ScoredCandidate,
)

if TYPE_CHECKING:
    from buysmarter.llm.review_summary import RequirementCheck, ReviewSummary


def apply_review_trust(scored: ScoredCandidate, trust: float, note: str) -> ScoredCandidate:
    """Return a copy of ``scored`` with its reviews axis rescaled by review trust.

    ``trust`` (0.3-1.0, from ``ranking.review_trust``) composes with the brand
    credibility factor already baked into the axis via ``min()`` — a fake-review
    penalty only tightens the factor, never loosens it. The trust note is
    appended to the reviews-axis explanation when ``trust < 1.0``.
    """
    credibility = credibility_factor(scored.candidate)
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
_RELIABILITY_CON = "отзывы: надёжность под вопросом"


def _top_red_flag(summary: "ReviewSummary") -> str:
    """The headline red-flag con text (first structured flag, else a generic)."""
    return summary.red_flags[0] if summary.red_flags else _RELIABILITY_CON


def _counterfeit_penalty(candidate: Candidate) -> tuple[float, str]:
    """Counterfeit (factor, con) tuned by seller multiplicity of the card."""
    if candidate.offer_count > _MULTI_SELLER_THRESHOLD:
        return _COUNTERFEIT_FACTOR_MULTI, COUNTERFEIT_CON_MULTI
    return _COUNTERFEIT_FACTOR, COUNTERFEIT_CON


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


def _requirement_notes(
    scored: ScoredCandidate,
    confirmed: list["RequirementCheck"],
    refuted: list["RequirementCheck"],
) -> tuple[list[str], list[str]]:
    """Pros quoting confirmations (replacing their "unverified" cons), cons quoting refutations."""
    resolved_cons = {UNVERIFIED_FEATURE_CON.format(feature=c.feature) for c in confirmed}
    cons = [con for con in scored.cons if con not in resolved_cons]
    cons.extend(_REFUTED_CON.format(feature=c.feature, quote=c.quote) for c in refuted)
    pros = [
        *scored.pros,
        *(_CONFIRMED_PRO.format(feature=c.feature, quote=c.quote) for c in confirmed),
    ]
    return pros, cons


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
    pros, cons = _requirement_notes(scored, confirmed, refuted)
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


# Reviews calling the item a fake (``review_trust.count_counterfeit_mentions``), over the
# fetched sample incl. the complaint-heavy negative page, judged against ALL the card's
# reviews: two mentions that are at least 1% of them, or any share of 5%+. A hit-seller
# card with 3000 reviews is not condemned by two angry buyers.
_COUNTERFEIT_MIN_MENTIONS = 2
_COUNTERFEIT_MIN_SHARE = 0.01
_COUNTERFEIT_SURE_SHARE = 0.05
_COUNTERFEIT_QUOTE_CON = "в отзывах пишут о подделке: {mentions} из {total}"


def _counterfeit_verdict(mentions: int, total: int) -> bool:
    share = mentions / max(total, mentions, 1)
    if share >= _COUNTERFEIT_SURE_SHARE:
        return True
    return mentions >= _COUNTERFEIT_MIN_MENTIONS and share >= _COUNTERFEIT_MIN_SHARE


def apply_counterfeit_mentions(
    scored: ScoredCandidate, mentions: int, total: int
) -> ScoredCandidate:
    """Bar and discount a candidate whose reviews call it a fake.

    Same hit as a counterfeit review summary (``_counterfeit_penalty``: softer on a
    multi-seller card), plus a con with the count. Unchanged below the threshold
    or when a summary already flagged it.
    """
    if mentions <= 0 or COUNTERFEIT_CON in scored.cons or COUNTERFEIT_CON_MULTI in scored.cons:
        return scored
    total = max(total, mentions)
    if not _counterfeit_verdict(mentions, total):
        return scored
    factor, con = _counterfeit_penalty(scored.candidate)
    new_total = round(scored.total_score * factor, 2)
    return scored.model_copy(
        update={
            "total_score": new_total,
            "confidence": round(scored.confidence * factor, 2),
            "cons": [
                *scored.cons,
                con,
                _COUNTERFEIT_QUOTE_CON.format(mentions=mentions, total=total),
            ],
            "review_penalty": round(scored.review_penalty + scored.total_score - new_total, 2),
        }
    )
