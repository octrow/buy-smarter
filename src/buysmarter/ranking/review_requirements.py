"""No-LLM requirement check: does a required feature show up in reviews? (B6).

Stem matching only. A feature is "confirmed" when enough reviews have a sentence
with every feature stem and no negation before it. It is never "refuted" here:
telling "no" from "doesn't work" needs a model, so a missing mention stays
"insufficient". The quote is real review text, so ``verify_checks`` accepts it.
"""

import re

from market_sources.base import RawReview

from buysmarter.llm.review_summary import MIN_MENTIONS, RequirementCheck
from buysmarter.ranking.review_trust import (
    feature_stems,
    filter_junk,
    is_defect_text,
    normalize_text,
)

# ponytail: any negation word anywhere in the sentence disqualifies it ("не
# работает", "без шумоподавления"), and so does a defect complaint. Loses some
# true positives ("не греется, шумоподавление отличное"); no parser. Scope the negation to
# the clause naming the feature at the first confirmed feature a real run reports unconfirmed.
_NEGATION_RE = re.compile(r"\b(не|нет|без|ни)\b")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n]+")


def _sentence_confirms(sentence: str, stems: list[str]) -> bool:
    """True when the sentence holds every stem, no negation and no defect complaint."""
    text = normalize_text(sentence)
    if any(stem not in text for stem in stems):
        return False
    return _NEGATION_RE.search(text) is None and not is_defect_text(text)


def _matching_sentence(review_text: str, stems: list[str]) -> str:
    """The first confirming sentence of one review, as written, or ``""``."""
    for raw in _SENTENCE_SPLIT_RE.split(review_text):
        sentence = raw.strip()
        if sentence and _sentence_confirms(sentence, stems):
            return sentence
    return ""


def _naive_check(feature: str, reviews: list[RawReview]) -> RequirementCheck:
    stems = feature_stems(feature)
    if not stems:
        return RequirementCheck(feature=feature)
    quotes = [q for q in (_matching_sentence(r.text, stems) for r in reviews) if q]
    if len(quotes) < MIN_MENTIONS:
        return RequirementCheck(feature=feature, mentions=len(quotes))
    return RequirementCheck(
        feature=feature, verdict="confirmed", mentions=len(quotes), quote=quotes[0]
    )


def naive_requirement_checks(
    reviews: list[RawReview], features: list[str]
) -> list[RequirementCheck]:
    """One stem-based check per feature over the non-junk reviews."""
    kept, _ = filter_junk(reviews)
    return [_naive_check(feature, kept) for feature in features]
