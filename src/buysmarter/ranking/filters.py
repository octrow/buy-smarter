"""Hard filters: budget, brand, availability, required features and active substance."""

import re
from typing import TYPE_CHECKING

from buysmarter.knowledge.ingredients import composition_text
from buysmarter.ranking.audience import other_audience
from buysmarter.ranking.models import (
    Candidate,
    QueryConstraints,
)

if TYPE_CHECKING:
    pass

CONCENTRATION_RE = re.compile(r"\d+[.,]?\d*\s*%")

# Waterproof-rating synonyms Kaspi listings use instead of the "IPX<n>" code
# itself. Static bridge for v1 — real LLM/embedding-based feature matching is
# a later iteration (see docs/PLAN.md).

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
    # ingredient axis (``required_concentration``), not string-matched here.
    hard_features = [
        f
        for f in constraints.required_features
        if not (required_substances and CONCENTRATION_RE.fullmatch(f.strip()))
    ]
    passed: list[Candidate] = []
    rejected: list[tuple[Candidate, str]] = []
    for candidate in candidates:
        reason = _listing_rejection(candidate, constraints, excluded_brands) or _content_rejection(
            candidate, hard_features, required_substances, substances_verified
        )
        if reason is None:
            passed.append(candidate)
        else:
            rejected.append((candidate, reason))

    return passed, rejected


def _listing_rejection(
    candidate: Candidate, constraints: QueryConstraints, excluded_brands: set[str]
) -> str | None:
    """Reject on the listing itself: budget, excluded/unknown brand, availability, audience."""
    if constraints.budget_max is not None and candidate.price > constraints.budget_max:
        return f"price exceeds budget: {candidate.price} > {constraints.budget_max}"
    if candidate.brand and candidate.brand.lower() in excluded_brands:
        return f"excluded brand: '{candidate.brand}'"
    if constraints.trusted_brands_only and candidate.brand_tier is None:
        return "unknown brand (trusted-brands-only)"
    if not candidate.availability:
        return "not available"
    if constraints.gender:
        listing = " ".join([candidate.title, *candidate.category_path])
        other = other_audience(constraints.gender, listing)
        if other is not None:
            return f"other audience: {other}"
    return None


def _content_rejection(
    candidate: Candidate,
    hard_features: list[str],
    required_substances: list[str],
    substances_verified: bool,
) -> str | None:
    """Reject on what the product is: required features, required substance."""
    # An empty specs dict means specs weren't fetched (not that the
    # product lacks the feature) — don't reject on unverifiable data;
    # `score()` attaches a "не удалось проверить" con instead.
    if candidate.specs:
        missing = [f for f in hard_features if not _feature_present(f, candidate)]
        if missing:
            return f"missing required feature(s): {', '.join(missing)}"
    if (
        required_substances
        and substances_verified
        and _substance_proven_absent(candidate, required_substances)
    ):
        return f"{NO_SUBSTANCE_REASON}: {', '.join(required_substances)}"
    return None


def has_required_substance(candidate: Candidate, required_substances: list[str]) -> bool:
    """True when the candidate's detected substances include a required one."""
    return any(s in candidate.detected_substances for s in required_substances)


def _substance_proven_absent(candidate: Candidate, required_substances: list[str]) -> bool:
    """A required substance is *proven* absent: состав declared, substance not in it.

    No состав text means the data is missing (unverifiable), not that the
    substance is absent — that candidate is kept and flagged, never rejected.
    """
    if has_required_substance(candidate, required_substances):
        return False
    return bool(composition_text(candidate.specs))
