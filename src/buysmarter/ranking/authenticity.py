"""Authenticity level of an offer (CONTEXT.md "Authenticity level").

Rules only, no LLM: any counterfeit signal that bars an offer from roles makes it a
likely fake; the brand's own site is certainly original; an official seller or a brand
nobody copies is likely original; everything else is unclear.
"""

from enum import Enum

from buysmarter.ranking.models import OFFICIAL_SOURCES, ScoredCandidate
from buysmarter.ranking.selection import is_barred


class Authenticity(Enum):
    CERTAIN = "точно оригинал"
    LIKELY = "вероятно оригинал"
    UNCLEAR = "не ясно"
    FAKE = "вероятно подделка"


def authenticity(scored: ScoredCandidate) -> Authenticity:
    """The offer's authenticity level from its counterfeit signals, seller and brand."""
    if is_barred(scored):
        return Authenticity.FAKE
    candidate = scored.candidate
    if candidate.source in OFFICIAL_SOURCES:
        return Authenticity.CERTAIN
    if candidate.official_seller or candidate.counterfeit_risk == "low":
        return Authenticity.LIKELY
    return Authenticity.UNCLEAR
