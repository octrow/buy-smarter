"""LLM-generated review summary for the final best pick (docs/INIT_DATA.md).

One structured summarization call over up to 30 review texts, producing pros /
cons / common complaints / a one-line verdict. Runs on the OmniRoute
``free-stack`` combo by name: the ``sonnet`` alias mapped there anyway (first
rung gemini-3.8-flash), so naming the combo says what actually answers. Jev
re-decides the ranking signals (below). The adapter's mandatory cache makes
repeat calls free.
"""

import asyncio
import logging
import re
from typing import Literal, get_args

from market_sources.base import RawReview
from pydantic import BaseModel, Field

from buysmarter.llm.adapter import LlmConfigError, LlmError, generate
from buysmarter.llm.jev import system_one
from buysmarter.ranking.review_trust import feature_stems, filter_junk, normalize_text

logger = logging.getLogger(__name__)

# OmniRoute combo, not a Claude alias: free-stack is what "sonnet" resolved to
# (pylibs claude-cli COMBOS); text quality is enough at gemini-3.8-flash level.
SUMMARIZE_REVIEWS_MODEL = "free-stack"

# Cap on how many review texts we feed the model (docs/INIT_DATA.md "up to 30").
_MAX_REVIEWS = 30
# Each review text is cut to this many characters before it reaches the prompt.
_MAX_REVIEW_CHARS = 500
# Reviews at or below this star rating are listed first: they carry the complaints.
_LOW_RATING_MAX = 3
# A requirement verdict needs at least this many reviews backing its quote.
MIN_MENTIONS = 2

ReliabilityConcern = Literal["none", "minor", "serious"]
RequirementVerdict = Literal["confirmed", "refuted", "insufficient"]


class RequirementCheck(BaseModel):
    """What the reviews say about one feature the query requires (B1)."""

    feature: str
    verdict: RequirementVerdict = "insufficient"
    mentions: int = 0
    quote: str = ""


class ReviewSummary(BaseModel):
    """Structured summary of a product's reviews.

    Beyond the human-facing pros/cons, the summary carries structured *severity*
    signals that feed the ranking penalty loop (``ranking.apply_summary_penalty``):
    reviews suspecting a counterfeit product, or reporting early failure / safety /
    non-functioning issues, discount the score so a well-rated but complaint-ridden
    listing can no longer win on stars alone (docs/INIT_DATA.md anti-counterfeit).
    """

    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    common_complaints: list[str] = Field(default_factory=list)
    verdict_line: str = ""
    red_flags: list[str] = Field(default_factory=list)
    counterfeit_suspicion: bool = False
    reliability_concern: ReliabilityConcern = "none"
    negative_share_estimate: float = 0.0
    requirement_checks: list[RequirementCheck] = Field(default_factory=list)


def _is_low_rated(review: RawReview) -> bool:
    return review.rating is not None and review.rating <= _LOW_RATING_MAX


def _mentions_feature(review: RawReview, stems: list[str]) -> bool:
    text = normalize_text(review.text)
    return any(stem in text for stem in stems)


def _select_reviews(reviews: list[RawReview], features: list[str] | None = None) -> list[RawReview]:
    """Non-junk reviews capped at ``_MAX_REVIEWS``: low-rated first, then feature mentions.

    Stable sort, so each tier keeps its input order.
    """
    kept, _ = filter_junk(reviews)
    stems = [stem for feature in features or [] for stem in feature_stems(feature)]
    ordered = sorted(
        kept,
        key=lambda review: (not _is_low_rated(review), not _mentions_feature(review, stems)),
    )
    return ordered[:_MAX_REVIEWS]


def _review_line(review: RawReview) -> str:
    """Review text cut to ``_MAX_REVIEW_CHARS``, prefixed with its star rating if known."""
    text = review.text.strip()[:_MAX_REVIEW_CHARS]
    if review.rating is None:
        return text
    return f"[{review.rating:g}★] {text}"


def _requirement_block(features: list[str]) -> str:
    """Prompt section asking for one verdict per required feature (B1)."""
    listed = "\n".join(f"- {feature}" for feature in features)
    return (
        "Also check each required feature below against the reviews. For each, give:\n"
        "- feature: the feature name, copied exactly\n"
        "- verdict: 'confirmed' if reviews state the product has it or that it works; "
        "'refuted' if reviews state it lacks it or that it fails; 'insufficient' if "
        "reviews do not discuss it\n"
        "- mentions: number of reviews that mention it\n"
        "- quote: ONE short verbatim quote copied exactly from a review; empty string "
        "if insufficient\n"
        f"Required features:\n{listed}\n\n"
    )


def summarize_reviews_prompt(texts: list[str], features: list[str] | None = None) -> str:
    """Build the summarization prompt from pre-formatted review lines.

    Without ``features`` the text is byte-identical to the original template, so
    cached summaries (keyed on the prompt) stay valid.
    """
    joined = "\n".join(f"- {text}" for text in texts)
    checks = _requirement_block(features) if features else ""
    return (
        "Summarize the following product reviews for a shopper. Respond in "
        "Russian. Each review may start with its star rating like [2★]. Extract:\n"
        "- pros: concrete positives buyers mention (list)\n"
        "- cons: concrete negatives buyers mention (list)\n"
        "- common_complaints: recurring complaints worth flagging (list)\n"
        "- verdict_line: one short sentence summarizing the overall sentiment\n"
        "- red_flags: severe issues stated plainly (safety, allergic reactions, "
        "burns, breaks within a year, doesn't perform its core function). "
        "Each a short Russian phrase. Empty list if none.\n"
        "- counterfeit_suspicion: true ONLY if reviews suspect the product is "
        "fake/counterfeit (e.g. wrong consistency, suspiciously cheap, differs "
        "from the original)\n"
        "- reliability_concern: 'serious' if reviews report early failure, "
        "safety issues, or that it does not do its core job; 'minor' for smaller "
        "recurring durability gripes; 'none' otherwise\n"
        "- negative_share_estimate: your estimate (0.0-1.0) of the share of "
        "reviews that are clearly negative\n\n"
        f"{checks}"
        f"Reviews:\n{joined}"
    )


# A one-word "quote" ("работает", "да") matches almost any corpus.
_MIN_QUOTE_WORDS = 2


def _quote_holds(check: RequirementCheck, corpus: list[str]) -> bool:
    """A verdict stands only with a real quote, backed by enough mentions."""
    quote = normalize_text(check.quote)
    if check.mentions < MIN_MENTIONS or len(quote.split()) < _MIN_QUOTE_WORDS:
        return False
    return any(quote in text for text in corpus)


def _verify_one(
    feature: str, check: RequirementCheck | None, corpus: list[str]
) -> RequirementCheck:
    if check is None:
        return RequirementCheck(feature=feature)
    if check.verdict != "insufficient" and _quote_holds(check, corpus):
        return RequirementCheck(
            feature=feature,
            verdict=check.verdict,
            mentions=check.mentions,
            quote=check.quote.strip(),
        )
    return RequirementCheck(feature=feature, mentions=check.mentions)


def verify_checks(
    checks: list[RequirementCheck], reviews: list[RawReview], features: list[str]
) -> list[RequirementCheck]:
    """Keep one check per requested feature, downgrading any unbacked verdict (B4).

    The model's feature names are matched after ``normalize_text``; features it
    omitted come back as ``insufficient``; checks for unrequested features drop.
    """
    by_feature = {normalize_text(check.feature): check for check in checks}
    corpus = [normalize_text(review.text) for review in reviews]
    return [
        _verify_one(feature, by_feature.get(normalize_text(feature)), corpus)
        for feature in features
    ]


# --- Jev: the signals ranking consumes --------------------------------------
#
# The LLM keeps the shopper-facing text (pros, cons, verdict, red-flag phrases);
# Jev re-decides the fields ranking reads, with calibrated probabilities instead
# of a generated bool/label/count:
#   counterfeit_suspicion — one Noul over the review list;
#   reliability_concern   — one 3-level Score (none / minor / serious);
#   requirement_checks    — code finds every sentence naming the feature, Jev
#     labels each (confirms / refutes / unrelated), code counts reviews and copies
#     the best sentence verbatim as the quote. Jev never counts or generates
#     (docs.typesafe.ai jev-1.13 jaggedness).
# On any Jev failure the LLM's own values stay (the previous behaviour).

# ponytail: thresholds tuned on a hand-labelled Russian review set
# (docs/jev-migration.md §3); retune after 50 real runs or at the first miss seen in one.
COUNTERFEIT_MIN_NOUL = 0.5
# A sentence counts toward a verdict when Jev's pick holds at least this share.
SENTENCE_MIN_PROB = 0.6
# Token-budget guard: Jev ingests at most 32k of state + longest question.
_MAX_SENTENCE_QUESTIONS = 120

_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n]+")
_RELIABILITY_LEVELS: tuple[ReliabilityConcern, ...] = get_args(ReliabilityConcern)

_COUNTERFEIT_QUESTION = {
    "type": "noul",
    "instructions": "Do any of the `reviews` suspect that the product is fake, counterfeit "
    "or not original?",
    "criteria": {
        "true": "A reviewer says or suspects it is a fake/copy/not original (wrong smell, "
        "consistency, packaging or markings, differs from the original, suspiciously cheap)",
        "false": "No reviewer questions authenticity; ordinary praise or complaints only",
    },
}
_RELIABILITY_QUESTION = {
    "type": "score",
    "instructions": {
        "question": "How serious are the reliability problems the `reviews` report?",
        "reliability_means": "the product breaking, failing, stopping working, wearing out, "
        "or being physically unsafe (overheating, burns, sparks, allergic reaction)",
        "not_reliability": "weak performance or quality while it still works, a missing or "
        "weak feature, noise, convenience gripes, price, and suspicion of a fake",
    },
    "criteria": [
        "No reliability problems reported (other complaints may exist)",
        "Minor durability gripes: parts wear, scuff or loosen, the product keeps working",
        "Serious: the product broke, stopped working or switching on within months, "
        "or reviewers report a physical safety issue",
    ],
}
_SENTENCE_CRITERIA = {
    "confirms": "The sentence states the product has the feature or that it works well",
    "refutes": "The sentence states the product lacks the feature, or that it fails or works badly",
    "unrelated": "The sentence does not say whether the product has the feature or how it works",
}


def _feature_sentences(texts: list[str], stems: list[str]) -> list[tuple[int, str]]:
    """``(review_index, sentence)`` for every sentence holding all ``stems``."""
    found = []
    for index, text in enumerate(texts):
        for raw in _SENTENCE_SPLIT_RE.split(text):
            sentence = raw.strip()
            if sentence and all(stem in normalize_text(sentence) for stem in stems):
                found.append((index, sentence))
    return found


def _sentence_question(i: int) -> dict:
    return {
        "type": "choice",
        "instructions": "What does `sentences[" + str(i) + "].text` say about the product "
        "feature `sentences[" + str(i) + "].feature`?",
        "criteria": _SENTENCE_CRITERIA,
    }


def _verdict(feature: str, labelled: list[tuple[int, str, str, float]]) -> RequirementCheck:
    """Count reviews per label; the side with ``MIN_MENTIONS``+ reviews and a majority wins."""
    reviews_by_label: dict[str, set[int]] = {"confirms": set(), "refutes": set()}
    best: dict[str, tuple[float, str]] = {}
    for review_index, sentence, label, prob in labelled:
        if label not in reviews_by_label or prob < SENTENCE_MIN_PROB:
            continue
        reviews_by_label[label].add(review_index)
        if prob > best.get(label, (0.0, ""))[0]:
            best[label] = (prob, sentence)
    confirms, refutes = len(reviews_by_label["confirms"]), len(reviews_by_label["refutes"])
    mentions = confirms + refutes
    if confirms >= MIN_MENTIONS and confirms > refutes:
        return RequirementCheck(
            feature=feature, verdict="confirmed", mentions=confirms, quote=best["confirms"][1]
        )
    if refutes >= MIN_MENTIONS and refutes > confirms:
        return RequirementCheck(
            feature=feature, verdict="refuted", mentions=refutes, quote=best["refutes"][1]
        )
    return RequirementCheck(feature=feature, mentions=mentions)


def build_questions(
    selected: list[RawReview], features: list[str]
) -> tuple[dict, dict[str, dict], list[tuple[str, int, str]]]:
    """State, questions, and the ``(feature, review_index, sentence)`` behind each ``s<i>``.

    Sentences come from the bare review text (no ``[2★]`` prefix) so a quote is
    verbatim review text and survives ``verify_checks``.
    """
    plain = [r.text.strip()[:_MAX_REVIEW_CHARS] for r in selected]
    sentences: list[tuple[str, int, str]] = [
        (feature, review_index, sentence)
        for feature in features
        if (stems := feature_stems(feature))
        for review_index, sentence in _feature_sentences(plain, stems)
    ][:_MAX_SENTENCE_QUESTIONS]
    state = {
        "reviews": [_review_line(r) for r in selected],
        "sentences": [{"feature": f, "text": s} for f, _, s in sentences],
    }
    questions = {"counterfeit": _COUNTERFEIT_QUESTION, "reliability": _RELIABILITY_QUESTION}
    questions |= {f"s{i}": _sentence_question(i) for i in range(len(sentences))}
    return state, questions, sentences


async def judge_reviews(
    selected: list[RawReview], features: list[str]
) -> tuple[bool, ReliabilityConcern, list[RequirementCheck]]:
    """One Jev request → ``(counterfeit, reliability, checks)`` (checks only for ``features``).

    Raises ``LlmError`` / ``LlmConfigError`` (from ``system_one``) or
    ``KeyError`` / ``TypeError`` / ``IndexError`` on a malformed answer.
    """
    state, questions, sentences = build_questions(selected, features)
    answers = await system_one(state, questions)
    counterfeit = answers["counterfeit"]["noul"] >= COUNTERFEIT_MIN_NOUL
    level = min(round(answers["reliability"]["score"]), len(_RELIABILITY_LEVELS) - 1)
    reliability = _RELIABILITY_LEVELS[max(level, 0)]
    labelled = {feature: [] for feature in features}
    for i, (feature, review_index, sentence) in enumerate(sentences):
        answer = answers[f"s{i}"]
        prob = answer["probabilities"][answer["choice"]]
        labelled[feature].append((review_index, sentence, answer["choice"], prob))
    checks = [_verdict(feature, labelled[feature]) for feature in features]
    return counterfeit, reliability, checks


async def _judge_or_none(
    selected: list[RawReview], features: list[str]
) -> tuple[bool, ReliabilityConcern, list[RequirementCheck]] | None:
    """``judge_reviews``, or ``None`` on any Jev failure (the LLM's values then stay)."""
    try:
        return await judge_reviews(selected, features)
    except (LlmError, LlmConfigError, KeyError, TypeError, IndexError) as exc:
        logger.warning("review Jev call failed (%r); keeping the LLM's signals", exc)
        return None


async def summarize_reviews(
    reviews: list[RawReview], features: list[str] | None = None
) -> ReviewSummary:
    """Summarize up to 30 non-junk review texts into a ``ReviewSummary``.

    The LLM writes the text and a first take on every field; Jev, run in
    parallel, overrides the ranking signals (counterfeit, reliability,
    requirement checks) when it answers. ``features`` are the query's required
    features still unverified by specs; their checks pass ``verify_checks``.

    Raises ``LlmError`` / ``LlmConfigError`` on an LLM failure — the caller
    decides whether to degrade gracefully (skip the panel).
    """
    features = features or []
    selected = _select_reviews(reviews, features)
    texts = [_review_line(r) for r in selected]
    if not texts:
        return ReviewSummary(requirement_checks=verify_checks([], [], features))
    result, judged = await asyncio.gather(
        generate(
            summarize_reviews_prompt(texts, features or None),
            ReviewSummary,
            model=SUMMARIZE_REVIEWS_MODEL,
        ),
        _judge_or_none(selected, features),
    )
    update: dict = {}
    checks = result.requirement_checks  # type: ignore[union-attr]
    if judged is not None:
        counterfeit, reliability, checks = judged
        update = {"counterfeit_suspicion": counterfeit, "reliability_concern": reliability}
    if features:
        update["requirement_checks"] = verify_checks(checks, selected, features)
    return result.model_copy(update=update) if update else result  # type: ignore[union-attr,return-value]
