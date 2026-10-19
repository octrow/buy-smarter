"""Pick the roles (best, best value, budget, premium) and the alternatives from a scored pool."""

import re
from collections.abc import Callable
from math import ceil
from typing import TYPE_CHECKING

from buysmarter.ranking.flags import (
    COUNTERFEIT_CON,
    COUNTERFEIT_CON_MULTI,
    DUP_PRICE_GAP_MARKER,
    SUSPICIOUS_PRICE_CON,
)
from buysmarter.ranking.models import (
    Recommendation,
    ScoredCandidate,
)

if TYPE_CHECKING:
    pass

_PREMIUM_TIERS = ("A", "B")

# best_value only qualifies candidates within this fraction of the best
# score — otherwise cheap junk (low score, tiny price) wins on score/price
# ratio alone regardless of how bad it actually is.
_BEST_VALUE_MIN_SCORE_RATIO = 0.8

# best_budget quality floor: a candidate must score at least this fraction of
# the best pick's total_score to qualify — otherwise the cheapest listing
# (possibly a counterfeit or scam) auto-wins on price alone regardless of how
# badly it scores everywhere else (docs/PLAN.md "Ranking-фиксы").
_BEST_BUDGET_MIN_SCORE_RATIO = 0.5


# Flavor/color vocabulary for ``variant_key``: a title differing only by these
# words is the same product in another flavor/color, not a distinct SKU.
# Prefixes cover Russian inflections (шоколадный/шоколадная); words that would
# over-match as a prefix (белый vs белок, серый vs сыворотка) are listed whole.
_VARIANT_PREFIXES = (
    "шоколад",
    "молочн",  # «молочный шоколад» — flavor, not a product line
    "ванил",
    "клубни",
    "банан",
    "карамел",
    "кокос",
    "печень",
    "фисташ",
    "вишн",
    "черник",
    "малин",
    "манг",
    "чёрн",
    "черн",
    "красн",
    "розов",
    "зелен",
    "зелён",
    "бежев",
    "коричнев",
    "фиолетов",
    "оранжев",
    "chocolate",
    "vanill",
    "strawberr",
    "banana",
    "caramel",
    "coconut",
    "cookie",
)
_VARIANT_WORDS = frozenset(
    {
        "вкус",
        "вкусом",
        "цвет",
        "цвета",
        "цветом",
        "белый",
        "белая",
        "белое",
        "синий",
        "синяя",
        "синее",
        "серый",
        "серая",
        "серое",
        "голубой",
        "голубая",
        "желтый",
        "жёлтый",
        "желтая",
        "жёлтая",
        "cream",
        "cookies",
        "black",
        "white",
        "blue",
        "pink",
        "gray",
        "grey",
        "green",
        "red",
        "yellow",
        "flavor",
        "flavour",
        "color",
        "colour",
        "и",
        "с",
        "and",
    }
)


def _is_variant_token(token: str) -> bool:
    return token in _VARIANT_WORDS or token.startswith(_VARIANT_PREFIXES)


def variant_key(title: str) -> str:
    """Title with flavor/color words and punctuation removed — two listings
    with equal keys are variants of one product (e.g. PhaseX chocolate vs
    banana-strawberry). Volume/weight tokens are kept: 930 г vs 2 кг differ."""
    tokens = re.sub(r"[^\w\s]", " ", title.lower()).split()
    return " ".join(t for t in tokens if not _is_variant_token(t))


def distinct_variants(scored: list[ScoredCandidate], n: int) -> list[ScoredCandidate]:
    """First ``n`` of ``scored`` with later color/flavor variants of an earlier
    product dropped — a top-N table of four colors of one guitar shows one."""
    seen: set[str] = set()
    out: list[ScoredCandidate] = []
    for s in scored:
        key = variant_key(s.candidate.title)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) == n:
            break
    return out


def _identity(scored: ScoredCandidate) -> tuple[str, str]:
    """Product identity for dedup: same source + source_product_id."""
    return (scored.candidate.source, scored.candidate.source_product_id)


def _is_barred_from_roles(scored: ScoredCandidate) -> bool:
    """True when a candidate must never be endorsed as a "best X" alternative
    (nor, per ``assemble_recommendation``, crowned ``best`` outright).

    Covers both suspicious-price variants (generic unknown-brand con and the
    dup-price-gap con), brand-mimicry candidates, and a review-summary
    counterfeit verdict — all stay visible in the ranked table, but none may
    win best/best_value/best_budget/best_premium.
    """
    if scored.candidate.mimic_brand is not None:
        return True
    # Dup-price-gap bars by its CON, not the raw field — the gap only counts
    # as suspicion when ``score()`` deemed the candidate market-cheap too.
    if any(DUP_PRICE_GAP_MARKER in c for c in scored.cons):
        return True
    if COUNTERFEIT_CON in scored.cons or COUNTERFEIT_CON_MULTI in scored.cons:
        return True
    return SUSPICIOUS_PRICE_CON in scored.cons


def is_barred(scored: ScoredCandidate) -> bool:
    """Public wrapper for ``_is_barred_from_roles`` — for callers outside this
    module (e.g. CLI/report renderers flagging a listing with a "⚠" marker)
    that should not reach into a private name."""
    return _is_barred_from_roles(scored)


class _RoleClaims:
    """Roles claimed so far, and the products and variants they took."""

    def __init__(self, best: ScoredCandidate) -> None:
        self.alternatives: dict[str, ScoredCandidate] = {}
        self.used = {_identity(best)}
        # Flavor/color variants of an already-picked product never take a role.
        self.used_variants = {variant_key(best.candidate.title)}

    def is_free(self, s: ScoredCandidate) -> bool:
        return (
            _identity(s) not in self.used
            and variant_key(s.candidate.title) not in self.used_variants
        )

    def claim(
        self,
        role: str,
        candidates: list[ScoredCandidate],
        key: Callable[[ScoredCandidate], float],
        reverse: bool,
    ) -> None:
        remaining = [s for s in candidates if self.is_free(s)]
        if not remaining:
            return
        chosen = (max if reverse else min)(remaining, key=key)
        self.alternatives[role] = chosen
        self.used.add(_identity(chosen))
        self.used_variants.add(variant_key(chosen.candidate.title))


def _premium_pool(
    eligible: list[ScoredCandidate], full: list[ScoredCandidate]
) -> list[ScoredCandidate]:
    """best_premium only ever goes to a known, trusted brand (tier A/B) — an
    unknown-brand candidate ("noname KERN") must never win this role.

    Prefer a premium-tier candidate from the top quartile of the full pool;
    fall back to any remaining premium-tier candidate if the quartile has
    none left (e.g. because the best pick itself was the sole tier-A/B
    member of that quartile).
    """
    quartile_n = max(1, ceil(len(full) * 0.25))
    top_quartile_ids = {
        _identity(s) for s in sorted(full, key=lambda s: s.total_score, reverse=True)[:quartile_n]
    }
    premium = [s for s in eligible if s.candidate.brand_tier in _PREMIUM_TIERS]
    return [s for s in premium if _identity(s) in top_quartile_ids] or premium


def _pick_alternatives(
    best: ScoredCandidate, pool: list[ScoredCandidate], full: list[ScoredCandidate]
) -> dict[str, ScoredCandidate]:
    """Pick role-labeled alternatives, each a distinct product from the best
    pick and from every other role — a role is dropped rather than repeat a
    product (e.g. a pool of one must not crown the best pick as its own
    "best value" alternative).
    """
    # Candidates flagged with a suspiciously-low price are barred from EVERY
    # alternative role: they remain in the ranked list/table with their con
    # visible, but must never be endorsed as "best budget/value/premium"
    # (docs/PLAN.md "Ranking-фиксы", anti-counterfeit).
    claims = _RoleClaims(best)
    eligible = [s for s in pool if _identity(s) not in claims.used and not _is_barred_from_roles(s)]

    # Only candidates close to the best score qualify for "best value" — a
    # low-scoring, suspiciously cheap product must not win on score/price
    # ratio alone.
    threshold = _BEST_VALUE_MIN_SCORE_RATIO * best.total_score
    # A price <= 0 is an invalid listing (see ``_score_price``): it would divide
    # by zero here and "win" best_budget as the cheapest, so it gets neither role.
    priced = [s for s in eligible if s.candidate.price > 0]
    claims.claim(
        "best_value",
        [s for s in priced if s.total_score >= threshold],
        key=lambda s: s.total_score / s.candidate.price,
        reverse=True,
    )

    # Quality floor: a candidate scoring far below the best pick must not win
    # best_budget on price alone (e.g. a counterfeit that scores 30/100).
    budget_threshold = _BEST_BUDGET_MIN_SCORE_RATIO * best.total_score
    claims.claim(
        "best_budget",
        [s for s in priced if s.total_score >= budget_threshold],
        key=lambda s: s.candidate.price,
        reverse=False,
    )
    claims.claim(
        "best_premium", _premium_pool(eligible, full), key=lambda s: s.candidate.price, reverse=True
    )
    return claims.alternatives


def _pick_best(scored: list[ScoredCandidate]) -> ScoredCandidate:
    """Top-scored unbarred, priced candidate; then any unbarred one; then the top score.

    A price <= 0 listing is invalid and must not be crowned; it is a last
    resort, like barred ones, only when nothing else exists.
    """
    unbarred = [s for s in scored if not _is_barred_from_roles(s)]
    return next((s for s in unbarred if s.candidate.price > 0), None) or next(
        iter(unbarred), scored[0]
    )


def assemble_recommendation(
    scored: list[ScoredCandidate],
    rejected_count: int,
    rejected_reasons: dict[str, int] | None = None,
) -> Recommendation:
    """Sort scored candidates and label the best + role alternatives.

    ``best`` is the highest-scoring candidate NOT barred by
    ``_is_barred_from_roles`` (suspicious price, dup-price-gap, brand mimicry,
    or a review-summary counterfeit verdict) — a counterfeit's inflated
    score/price ratio must never crown it the top pick. Falls back to the raw
    top score (``scored[0]``) only when every candidate in the pool is barred,
    i.e. there is nothing trustworthy left to prefer.

    Split out of ``rank`` so a caller can re-assemble after a second pass tweaks
    scores (e.g. the review-trust re-score in ``services.recommend``) without
    re-running hard filters or the whole scoring pipeline — such callers pass
    the ORIGINAL ``rejected_reasons`` back through unchanged (re-scoring never
    changes which candidates were hard-filtered out).
    """
    scored = sorted(scored, key=lambda s: s.total_score, reverse=True)
    best = _pick_best(scored)
    pool = [s for s in scored if s is not best] or scored
    alternatives = _pick_alternatives(best, pool, scored)
    return Recommendation(
        best=best,
        alternatives=alternatives,
        ranked=scored,
        rejected_count=rejected_count,
        rejected_reasons=rejected_reasons or {},
    )
