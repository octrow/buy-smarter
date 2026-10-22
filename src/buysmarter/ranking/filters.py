"""Hard filters: budget, brand, availability, audience, required features, extra checks."""

import re
from collections.abc import Callable

from buysmarter.ranking.audience import other_audience
from buysmarter.ranking.models import (
    Candidate,
    QueryConstraints,
    RankHooks,
)

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

_NO_HOOKS = RankHooks()


def apply_hard_filters(
    candidates: list[Candidate],
    constraints: QueryConstraints,
    hooks: RankHooks = _NO_HOOKS,
) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    """Split candidates into (passed, rejected-with-reason).

    Checks, in order: budget, excluded brand, unknown brand when trusted-only,
    availability, audience, the extra listing checks passed in, required features
    (as the passed-in adjusters leave them), the extra content checks. First
    failing check wins the reason.
    """
    excluded_brands = {b.lower() for b in constraints.excluded_brands}
    hard_features = list(constraints.required_features)
    for adjust in hooks.hard_features:
        hard_features = adjust(hard_features)
    passed: list[Candidate] = []
    rejected: list[tuple[Candidate, str]] = []
    for candidate in candidates:
        reason = (
            _listing_rejection(candidate, constraints, excluded_brands)
            or _first(hooks.reject_listing, listing_text(candidate))
            or _feature_rejection(candidate, hard_features)
            or _first(hooks.reject_content, candidate)
        )
        if reason is None:
            passed.append(candidate)
        else:
            rejected.append((candidate, reason))

    return passed, rejected


def _first[T](checks: tuple[Callable[[T], str | None], ...], value: T) -> str | None:
    """The first reason any check gives for ``value``."""
    return next((reason for check in checks if (reason := check(value)) is not None), None)


def listing_text(candidate: Candidate) -> str:
    """What a listing says about itself: the title and the marketplace's category path."""
    return " ".join([candidate.title, *candidate.category_path])


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
        other = other_audience(constraints.gender, listing_text(candidate))
        if other is not None:
            return f"other audience: {other}"
    return None


def _feature_rejection(candidate: Candidate, hard_features: list[str]) -> str | None:
    """Reject on what the product is: a required feature its specs lack."""
    # An empty specs dict means specs weren't fetched (not that the
    # product lacks the feature) — don't reject on unverifiable data;
    # `score()` attaches a "не удалось проверить" con instead.
    if candidate.specs:
        missing = [f for f in hard_features if not _feature_present(f, candidate)]
        if missing:
            return f"missing required feature(s): {', '.join(missing)}"
    return None
