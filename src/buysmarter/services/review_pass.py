"""Second ranking pass over the top picks: fetch their reviews, discount fake-looking
review axes, re-sort, then summarize the best pick and check required features in reviews.
"""

import asyncio
import inspect
import logging
from collections.abc import Sequence
from typing import NamedTuple

from market_sources.base import ProductSource, RawReview, RawReviewStats

from buysmarter.llm.adapter import LlmConfigError, LlmError
from buysmarter.llm.review_summary import RequirementCheck, ReviewSummary, summarize_reviews
from buysmarter.ranking.filters import feature_present
from buysmarter.ranking.models import (
    Recommendation,
    ScoredCandidate,
)
from buysmarter.ranking.penalties import (
    apply_counterfeit_mentions,
    apply_defect_penalty,
    apply_requirement_checks,
    apply_review_trust,
    apply_summary_penalty,
)
from buysmarter.ranking.review_requirements import naive_requirement_checks
from buysmarter.ranking.review_trust import (
    count_counterfeit_mentions,
    count_defect_complaints,
    review_trust,
)
from buysmarter.ranking.selection import assemble_recommendation

logger = logging.getLogger(__name__)


# Review-trust re-score / summarization only pay for reviews on the top ranked
# Kaspi candidates (Kaspi's review endpoint is cheap; Ozon's is browser-heavy).
# Each costs two calls (default + negative page) for the counterfeit check.
_REVIEW_TRUST_TOP_N = 6
_REVIEW_SOURCE = "kaspi"
_REVIEW_FETCH_CONCURRENCY = 3  # parallel trust-pass review fetches

ReviewsById = dict[tuple[str, str], list[RawReview]]
StatsById = dict[tuple[str, str], RawReviewStats]


def _candidate_key(scored: ScoredCandidate) -> tuple[str, str]:
    return (scored.candidate.source, scored.candidate.source_product_id)


async def _reviews_and_stats(
    source: ProductSource, product_id: str
) -> tuple[list[RawReview], RawReviewStats | None]:
    """Reviews plus aggregate stats when the source has them (Kaspi), else no stats."""
    with_stats = getattr(source, "reviews_with_stats", None)
    if with_stats is None:
        return await source.reviews(product_id), None
    result: tuple[list[RawReview], RawReviewStats | None] = await with_stats(product_id)
    return result


async def _negative_reviews(source: ProductSource, product_id: str) -> list[RawReview]:
    """The low-rated reviews page, where "подделка/реплика" complaints live.

    The default page is sorted by popularity and is mostly praise. Sources
    without a negative filter (older adapters, test doubles) give nothing.
    """
    reviews = getattr(source, "reviews", None)
    if reviews is None or "negative" not in inspect.signature(reviews).parameters:
        return []
    try:
        result: list[RawReview] = await reviews(product_id, negative=True)
        return result
    except Exception as exc:  # noqa: BLE001 - a review fetch must not kill the run
        logger.warning("Negative review fetch failed for %s: %r", product_id, exc)
        return []


def _union(first: list[RawReview], second: list[RawReview]) -> list[RawReview]:
    """Both review lists without the reviews they share."""
    seen = {(r.author, r.text) for r in first}
    return first + [r for r in second if (r.author, r.text) not in seen]


class _Fetched(NamedTuple):
    reviews: ReviewsById
    stats: StatsById
    # Default page + negative page, for counting "подделка/реплика" mentions.
    all_reviews: ReviewsById


async def _fetch_top_reviews(source: ProductSource, recommendation: Recommendation) -> _Fetched:
    """Fetch reviews (and stats) for the top ranked Kaspi candidates, best-effort per id."""
    top = [s for s in recommendation.ranked if s.candidate.source == _REVIEW_SOURCE]
    semaphore = asyncio.Semaphore(_REVIEW_FETCH_CONCURRENCY)

    async def fetch(
        scored: ScoredCandidate,
    ) -> tuple[list[RawReview], RawReviewStats | None, list[RawReview]] | None:
        product_id = scored.candidate.source_product_id
        async with semaphore:
            try:
                reviews, stats = await _reviews_and_stats(source, product_id)
            except Exception as exc:  # noqa: BLE001 - a review fetch must not kill the run
                logger.warning("Review fetch failed for %s: %r", product_id, exc)
                return None
            return reviews, stats, await _negative_reviews(source, product_id)

    selected = top[:_REVIEW_TRUST_TOP_N]
    fetched = await asyncio.gather(*(fetch(scored) for scored in selected))
    result = _Fetched({}, {}, {})
    for scored, outcome in zip(selected, fetched, strict=True):
        if outcome is None:
            continue
        reviews, stats, negative = outcome
        key = _candidate_key(scored)
        result.reviews[key] = reviews
        result.all_reviews[key] = _union(reviews, negative)
        if stats is not None:
            result.stats[key] = stats
    return result


def _apply_review_trust(
    recommendation: Recommendation,
    reviews_by_id: ReviewsById,
    stats_by_id: StatsById | None = None,
) -> Recommendation:
    """Re-score low-trust and defect-complaint candidates, then re-assemble.

    Small explicit second pass: per fetched candidate, rescale the reviews axis
    by its trust multiplier (when < 1.0) and apply the defect-complaint penalty.
    Re-sorts only when something changed.
    """
    stats_by_id = stats_by_id or {}
    adjusted: dict[tuple[str, str], ScoredCandidate] = {}
    for scored in recommendation.ranked:
        key = _candidate_key(scored)
        reviews = reviews_by_id.get(key)
        if not reviews:
            continue
        trust, note = review_trust(reviews, stats_by_id.get(key))
        updated = apply_review_trust(scored, trust, note) if trust < 1.0 else scored
        # ponytail: complaint share uses the fetched sample (~9 Kaspi reviews), not stats.total.
        # Fetch more review pages at the first best pick decided by this penalty on < 20 reviews.
        updated = apply_defect_penalty(updated, count_defect_complaints(reviews), len(reviews))
        if updated is not scored:
            adjusted[key] = updated
    if not adjusted:
        return recommendation
    rescored = [adjusted.get(_candidate_key(s), s) for s in recommendation.ranked]
    return assemble_recommendation(
        rescored, recommendation.rejected_count, recommendation.rejected_reasons
    )


_REVIEWS_NOT_LOADED_CON = "тексты отзывов не загрузились — подделку по отзывам не проверить"


def _counterfeit_check(scored: ScoredCandidate, fetched: _Fetched) -> ScoredCandidate:
    """Counterfeit mentions over a fetched candidate's reviews, or a con when none loaded."""
    key = _candidate_key(scored)
    reviews = fetched.all_reviews.get(key)
    if reviews is None:
        return scored
    if not reviews:
        if scored.candidate.review_count:
            return scored.model_copy(update={"cons": [*scored.cons, _REVIEWS_NOT_LOADED_CON]})
        return scored
    stats = fetched.stats.get(key)
    total = (stats.total if stats else None) or scored.candidate.review_count or len(reviews)
    return apply_counterfeit_mentions(scored, count_counterfeit_mentions(reviews), total)


def _apply_counterfeit_checks(recommendation: Recommendation, fetched: _Fetched) -> Recommendation:
    """Bar candidates whose reviews call them fakes, then re-assemble when anything changed."""
    rescored = [_counterfeit_check(s, fetched) for s in recommendation.ranked]
    if all(new is old for new, old in zip(rescored, recommendation.ranked, strict=True)):
        return recommendation
    return assemble_recommendation(
        rescored, recommendation.rejected_count, recommendation.rejected_reasons
    )


# Review summaries (the sonnet call) only cover the top ranked candidates — at
# most this many, so a run never pays for more than a handful of summary calls.
# Candidates with fewer reviews than the floor are skipped (too thin to trust).
_REVIEW_SUMMARY_TOP_N = 3
_SUMMARY_CONCURRENCY = 3  # parallel review-summary LLM calls
_MIN_REVIEWS_FOR_SUMMARY = 5
_NOT_SUMMARIZED = f"лучший выбор не вошёл в топ-{_REVIEW_SUMMARY_TOP_N}, чьи отзывы сводятся"

SummariesById = dict[tuple[str, str], ReviewSummary]
# A summary, or the reason a candidate has none.
_SummaryOutcomes = dict[tuple[str, str], ReviewSummary | str]


async def _reviews_for(
    scored: ScoredCandidate, active: dict[str, ProductSource], reviews_by_id: ReviewsById
) -> list[RawReview]:
    """Reviews for one candidate: reuse the trust-pass fetch, else its own source.

    The trust pass only fetches Kaspi reviews, so a top-3 candidate from another
    source (e.g. an Ozon listing) has its reviews fetched here — best-effort, a
    failure just yields no reviews (and thus no summary).
    """
    cached = reviews_by_id.get(_candidate_key(scored))
    if cached is not None:
        return cached
    source = active.get(scored.candidate.source)
    if source is None:
        return []
    try:
        return await source.reviews(scored.candidate.source_product_id)
    except Exception as exc:  # noqa: BLE001 - a review fetch must not kill the run
        logger.warning("Review fetch failed for summary: %r", exc)
        return []


def _unverified_features(scored: ScoredCandidate, required_features: Sequence[str]) -> list[str]:
    """Required features the candidate's title/specs do not already show."""
    return [f for f in required_features if not feature_present(f, scored.candidate)]


async def _summarize_top(
    recommendation: Recommendation,
    active: dict[str, ProductSource],
    reviews_by_id: ReviewsById,
    required_features: Sequence[str] = (),
) -> _SummaryOutcomes:
    """Summarize the top-N ranked candidates' reviews (≥ floor), degrading gracefully."""
    semaphore = asyncio.Semaphore(_SUMMARY_CONCURRENCY)

    async def summarize(scored: ScoredCandidate) -> ReviewSummary | str:
        """The summary, or why there is none (shown to the user as is)."""
        async with semaphore:
            reviews = await _reviews_for(scored, active, reviews_by_id)
            if len(reviews) < _MIN_REVIEWS_FOR_SUMMARY:
                need = _MIN_REVIEWS_FOR_SUMMARY
                return f"мало текстов отзывов для сводки ({len(reviews)}, нужно от {need})"
            features = _unverified_features(scored, required_features)
            try:
                return await summarize_reviews(reviews, features)
            except (LlmError, LlmConfigError) as exc:
                logger.warning("Review summarization failed (%r); skipping candidate", exc)
                return "AI не смог свести отзывы (подробности в логе)"

    selected = recommendation.ranked[:_REVIEW_SUMMARY_TOP_N]
    results = await asyncio.gather(*(summarize(scored) for scored in selected))
    return {_candidate_key(scored): out for scored, out in zip(selected, results, strict=True)}


def _apply_summary_penalties(
    recommendation: Recommendation, summaries: SummariesById
) -> Recommendation:
    """Penalize candidates whose summary carries severe signals, then re-assemble.

    Counterfeit suspicion / serious reliability concerns discount the score (and
    confidence) via ``apply_summary_penalty``; re-sorting after may change the
    best pick — that is the whole point. Untouched when no summary bites.
    """
    adjusted: dict[tuple[str, str], ScoredCandidate] = {}
    for scored in recommendation.ranked:
        summary = summaries.get(_candidate_key(scored))
        if summary is None:
            continue
        penalized = apply_summary_penalty(scored, summary)
        if penalized is not scored:
            adjusted[_candidate_key(scored)] = penalized
    if not adjusted:
        return recommendation
    rescored = [adjusted.get(_candidate_key(s), s) for s in recommendation.ranked]
    return assemble_recommendation(
        rescored, recommendation.rejected_count, recommendation.rejected_reasons
    )


def _apply_requirement_checks(
    recommendation: Recommendation, checks_by_id: dict[tuple[str, str], list[RequirementCheck]]
) -> Recommendation:
    """Fold review verdicts on required features into the ranking, then re-assemble."""
    adjusted: dict[tuple[str, str], ScoredCandidate] = {}
    for scored in recommendation.ranked:
        checks = checks_by_id.get(_candidate_key(scored))
        if not checks:
            continue
        updated = apply_requirement_checks(scored, checks)
        if updated is not scored:
            adjusted[_candidate_key(scored)] = updated
    if not adjusted:
        return recommendation
    rescored = [adjusted.get(_candidate_key(s), s) for s in recommendation.ranked]
    return assemble_recommendation(
        rescored, recommendation.rejected_count, recommendation.rejected_reasons
    )


def _naive_checks_by_id(
    recommendation: Recommendation, reviews_by_id: ReviewsById, required_features: Sequence[str]
) -> dict[tuple[str, str], list[RequirementCheck]]:
    """No-LLM stem checks over the fetched reviews of each ranked candidate."""
    checks_by_id: dict[tuple[str, str], list[RequirementCheck]] = {}
    for scored in recommendation.ranked:
        key = _candidate_key(scored)
        reviews = reviews_by_id.get(key)
        features = _unverified_features(scored, required_features)
        if reviews and features:
            checks_by_id[key] = naive_requirement_checks(reviews, features)
    return checks_by_id


async def review_pass(
    recommendation: Recommendation,
    active: dict[str, ProductSource],
    use_llm: bool,
    with_review_summary: bool,
    required_features: Sequence[str] = (),
) -> tuple[Recommendation, ReviewSummary | None, str | None]:
    """Fetch reviews, apply the Kaspi trust re-score, then summarize + penalize.

    Returns the recommendation, the best pick's summary, and why it has none
    (``None`` when it has one or summaries were not asked for).

    Runs while sources are still open. The trust re-score is Kaspi-only (cheap
    endpoint). Summaries cover the top-3 ranked candidates (whatever their
    source); their severity signals penalize scores and may change the best pick.
    The returned summary is the (possibly new) best pick's — that drives the panel.
    """
    reviews_by_id: ReviewsById = {}
    kaspi = active.get(_REVIEW_SOURCE)
    if kaspi is not None:
        fetched = await _fetch_top_reviews(kaspi, recommendation)
        reviews_by_id = fetched.reviews
        recommendation = _apply_review_trust(recommendation, reviews_by_id, fetched.stats)
        recommendation = _apply_counterfeit_checks(recommendation, fetched)
    review_summary = None
    missing_note = None
    if with_review_summary and use_llm:
        outcomes = await _summarize_top(recommendation, active, reviews_by_id, required_features)
        summaries = {k: v for k, v in outcomes.items() if isinstance(v, ReviewSummary)}
        recommendation = _apply_summary_penalties(recommendation, summaries)
        recommendation = _apply_requirement_checks(
            recommendation, {key: s.requirement_checks for key, s in summaries.items()}
        )
        outcome = outcomes.get(_candidate_key(recommendation.best), _NOT_SUMMARIZED)
        review_summary = outcome if isinstance(outcome, ReviewSummary) else None
        missing_note = outcome if isinstance(outcome, str) else None
    if not use_llm and required_features:
        checks_by_id = _naive_checks_by_id(recommendation, reviews_by_id, required_features)
        recommendation = _apply_requirement_checks(recommendation, checks_by_id)
    return recommendation, review_summary, missing_note
