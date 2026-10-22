"""Composition capability: medicines, cosmetics, supplements, judged by the active substance.

Switched on by the category profile (an ingredient book). It searches by the substances
the query names, protects title hits from the relevance filter, reads the composition
(title first, then one LLM extraction over the pool), refuses a pool proven to lack the
substance (``NoSubstanceMatch``), rejects products whose composition proves it absent,
scores the ingredient axis and names generic equivalents (docs/PLAN.md "Composition-driven
категории", docs/plans/2026-10-22-composition-fixes.md).
"""

import re
from typing import Any

from pydantic import BaseModel, Field

from buysmarter.capabilities.base import Capability, CapabilityContext, HardFilters, Section
from buysmarter.knowledge.ingredients import (
    IngredientBook,
    composition_text,
    normalize_concentration,
)
from buysmarter.knowledge.profile import (
    COMPOSITION,
    INGREDIENT_KEY,
    CapabilitySetting,
    ResolvedProfile,
)
from buysmarter.llm.ingredients import extract_ingredients
from buysmarter.ranking.models import (
    AxisScore,
    Candidate,
    QueryConstraints,
    Recommendation,
)

CONCENTRATION_RE = re.compile(r"\d+[.,]?\d*\s*%")
# Reason prefix for a candidate rejected because it lacks the explicitly required
# active substance. ``NoSubstanceMatch`` keys the honest empty-pool message off it.
NO_SUBSTANCE_REASON = "нет требуемого вещества"
INGREDIENTS_UNCHECKED = "ingredients_unchecked"

# Substance-targeted recall: a plain "крем с адапаленом" search on Kaspi is dominated by
# moisturizers "for use during acne treatment"; the actual adapalene products hide behind
# their brand names (Клензит/Дифферин/Адаклин), so each substance and equivalent is searched.
_MAX_SUBSTANCE_SEARCHES = 5  # total extra searches across all required substances
_SUBSTANCE_ALTERNATIVES_TOP_N = 3

# Credit for the ingredient axis: required substance present with a matching (or simply
# known) concentration is full credit; present but unknown/mismatched concentration 0.7;
# absent 0. Not checked (no extraction, or no answer for this candidate): the same honest
# partial credit for everyone, so the axis does not reorder an unchecked pool.
_INGREDIENT_PRESENT_UNKNOWN_CONC = 0.7
_INGREDIENT_UNCHECKED_CREDIT = 0.3


class NoSubstanceMatch(Exception):
    """No candidate contained the explicitly required active substance.

    A product that lacks the requested active substance must never be
    recommended as the best pick (docs/PLAN.md); when the required-substance
    hard filter empties the pool we refuse to silently fall back and instead
    surface the nearest rejected-by-substance candidates honestly. Carries the
    substances required, how many candidates were checked, and those nearest
    alternatives so the CLI/MCP render the same clear message.
    """

    def __init__(self, substances: list[str], checked: int, alternatives: list[Candidate]) -> None:
        self.substances = substances
        self.checked = checked
        self.alternatives = alternatives
        super().__init__(self.message())

    def message(self) -> str:
        subs = ", ".join(self.substances)
        return (
            f"Не найдено товаров с действующим веществом '{subs}' "
            f"(проверено {self.checked} кандидатов). Ближайшие альтернативы без него:"
        )


class EquivalenceGroup(BaseModel):
    """Pool members sharing one active substance (a generic-equivalence group).

    ``cheapest``/``priciest`` identify the extremes by price so the ingredient
    axis can attach the "аналог по действующему веществу" pros (docs/PLAN.md).
    Identities are ``(source, source_product_id)`` pairs.
    """

    cheapest_id: tuple[str, str]
    cheapest_name: str
    cheapest_price: int
    priciest_id: tuple[str, str]
    priciest_name: str
    priciest_price: int
    size: int


class IngredientContext(BaseModel):
    """Pool-wide active-substance context for the ingredient axis.

    Computed once per ranking run over the passed pool. ``required_substances``
    are the canonical substances the query targets; ``groups`` maps a substance to
    its in-pool equivalence group when it has more than one member.
    """

    required_substances: list[str] = Field(default_factory=list)
    groups: dict[str, EquivalenceGroup] = Field(default_factory=dict)


# --- Recall: substances in the query, searches, protected title hits ----------


def required_substances(book: IngredientBook, constraints: QueryConstraints) -> list[str]:
    """Canonical substances the query targets (from category + required features)."""
    text = " ".join([constraints.search_phrase, *constraints.required_features])
    return book.resolve_in_text(text)


def substance_search_plan(book: IngredientBook, required: list[str]) -> list[tuple[str, list[str]]]:
    """(query, title-needles) pairs for substance recall, capped and deduped by query."""
    plan: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for canonical in required:
        needles = book.title_needles_for(canonical)
        for term in book.search_terms_for(canonical):
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            plan.append((term, needles))
    return plan[:_MAX_SUBSTANCE_SEARCHES]


def substance_confirmed_ids(
    candidates: list[Candidate], book: IngredientBook, required: list[str]
) -> set[str]:
    """source_product_ids whose title names a required substance or its equivalent.

    These are the recall hits the category-relevance filter must not evict (an
    adapalene gel is the answer to "крем с адапаленом", form factor aside).
    """
    needles = [n.lower() for canonical in required for n in book.title_needles_for(canonical)]
    return {c.source_product_id for c in candidates if any(n in c.title.lower() for n in needles)}


# --- Reading the composition ---------------------------------------------------


def mark_title_substances(
    candidates: list[Candidate], book: IngredientBook, required: list[str]
) -> None:
    """A title naming a required substance (or its equivalent) checks it: detected, checked.

    ponytail: plain substring match, so "без адапалена" counts too; add negation
    handling if such titles show up in real runs.
    """
    for canonical in required:
        needles = [n.lower() for n in book.title_needles_for(canonical)]
        for cand in candidates:
            if not any(n in cand.title.lower() for n in needles):
                continue
            if canonical not in cand.detected_substances:
                cand.detected_substances = [*cand.detected_substances, canonical]
            cand.ingredients_checked = True


async def extract_ingredients_into(
    candidates: list[Candidate], category: str, book: IngredientBook, use_llm: bool
) -> None:
    """Populate ``detected_substances`` + ``concentration`` on each candidate.

    One Jev request over the pool (LLM fallback, see ``llm/ingredients.py``);
    detected names are mapped to the book's canonical substances and added to
    any the title already confirmed. Only a candidate that got an answer is marked
    ``ingredients_checked``; a failure leaves the rest unchecked, never "absent".
    """
    if not use_llm or not candidates:
        return
    items = [(c.title, composition_text(c.specs)) for c in candidates]
    detections = await extract_ingredients(category, book, items)
    for cand, det in zip(candidates, detections, strict=True):
        if det is None:
            continue
        canonicals = [book.canonical_for(s) or s for s in det.substances]
        found = [*cand.detected_substances, *canonicals]
        cand.detected_substances = list(dict.fromkeys(c for c in found if c))
        cand.concentration = det.concentration or cand.concentration
        cand.ingredients_checked = True


def guard_substance_pool(
    candidates: list[Candidate], required: list[str], hard_filters: HardFilters
) -> None:
    """Raise ``NoSubstanceMatch`` if the required-substance filter empties the pool.

    Only fires when the pool empties *because of* the substance filter — if it
    empties for another reason (e.g. budget), we stay silent and let ``rank``
    raise its generic "no candidates" error, preserving the existing message.
    """
    passed, rejected = hard_filters(candidates)
    if passed:
        return
    substance_rejects = [c for c, reason in rejected if reason.startswith(NO_SUBSTANCE_REASON)]
    if not substance_rejects:
        return
    top = sorted(
        substance_rejects,
        key=lambda c: (c.rating or 0.0, c.review_count or 0),
        reverse=True,
    )[:_SUBSTANCE_ALTERNATIVES_TOP_N]
    raise NoSubstanceMatch(required, len(candidates), top)


def has_required_substance(candidate: Candidate, required: list[str]) -> bool:
    """True when the candidate's detected substances include a required one."""
    return any(s in candidate.detected_substances for s in required)


def substance_proven_absent(candidate: Candidate, required: list[str]) -> bool:
    """A required substance is *proven* absent: checked, состав declared, substance not in it.

    Not checked (extraction off or without an answer) or no состав text means the
    data is missing, not that the substance is absent — kept and flagged, never rejected.
    """
    if not candidate.ingredients_checked or has_required_substance(candidate, required):
        return False
    return bool(composition_text(candidate.specs))


# --- The ingredient axis -------------------------------------------------------


def required_concentration(constraints: QueryConstraints) -> str | None:
    """A concentration named in the query's required features (e.g. "0.1%"), or None."""
    for feature in constraints.required_features:
        match = CONCENTRATION_RE.search(feature)
        if match:
            return match.group(0)
    return None


def _equivalence_group(candidates: list[Candidate], substance: str) -> EquivalenceGroup | None:
    """Cheapest vs priciest priced candidate sharing ``substance``; None without a price gap."""
    members = [c for c in candidates if substance in c.detected_substances and c.price > 0]
    if len(members) < 2:
        return None
    cheapest = min(members, key=lambda c: c.price)
    priciest = max(members, key=lambda c: c.price)
    if cheapest.price >= priciest.price:
        return None
    return EquivalenceGroup(
        cheapest_id=(cheapest.source, cheapest.source_product_id),
        cheapest_name=cheapest.title,
        cheapest_price=cheapest.price,
        priciest_id=(priciest.source, priciest.source_product_id),
        priciest_name=priciest.title,
        priciest_price=priciest.price,
        size=len(members),
    )


def build_ingredient_context(candidates: list[Candidate], required: list[str]) -> IngredientContext:
    """Pool-wide equivalence groups for the ingredient axis (pure, docs/PLAN.md).

    Targets the query's required substances, falling back to every substance seen
    in the pool. A group is recorded only when two+ candidates share the substance
    at different prices — that price gap is what powers the generic-equivalence
    "аналог по действующему веществу" advice.
    """
    present = {s for c in candidates for s in c.detected_substances}
    targets = [s for s in required if s in present] or sorted(present)
    groups = {s: g for s in targets if (g := _equivalence_group(candidates, s)) is not None}
    return IngredientContext(required_substances=required, groups=groups)


def score_ingredient(
    candidate: Candidate,
    ctx: IngredientContext,
    weights: dict[str, float],
    required_concentration: str | None,
) -> AxisScore:
    """Score the active-substance match: present+concentration full, unknown 0.7,
    unchecked 0.3, checked and absent 0."""
    weight = weights["ingredient"]
    required = ctx.required_substances
    present = (
        any(s in candidate.detected_substances for s in required)
        if required
        else bool(candidate.detected_substances)
    )
    if not present and not candidate.ingredients_checked:
        return AxisScore(
            name="ingredient",
            score=round(weight * _INGREDIENT_UNCHECKED_CREDIT, 2),
            max_score=weight,
            explanation="состав не проверен — вещество не подтверждено",
        )
    if not present:
        # Checked: with a состав this is proven absence (normally hard-filtered
        # already); without one, the title did not name the substance either.
        why = (
            "требуемое действующее вещество отсутствует"
            if composition_text(candidate.specs)
            else "вещество не найдено, состав не указан"
        )
        return AxisScore(name="ingredient", score=0.0, max_score=weight, explanation=why)
    conc = candidate.concentration
    if conc is None:
        credit, why = _INGREDIENT_PRESENT_UNKNOWN_CONC, "вещество есть, концентрация неизвестна"
    elif required_concentration and normalize_concentration(conc) != normalize_concentration(
        required_concentration
    ):
        credit = _INGREDIENT_PRESENT_UNKNOWN_CONC
        why = f"вещество есть, концентрация {conc} ≠ {required_concentration}"
    else:
        credit, why = 1.0, f"действующее вещество присутствует ({conc})"
    return AxisScore(
        name="ingredient", score=round(weight * credit, 2), max_score=weight, explanation=why
    )


def equivalence_verdicts(candidate: Candidate, ctx: IngredientContext) -> list[str]:
    """Generic-equivalence pros: cheapest generic vs the pricey brand (docs/PLAN.md)."""
    identity = (candidate.source, candidate.source_product_id)
    pros: list[str] = []
    for substance, group in ctx.groups.items():
        if substance not in candidate.detected_substances:
            continue
        if tuple(group.cheapest_id) == identity:
            pros.append(f"дженерик-аналог {group.priciest_name} по той же формуле")
        elif candidate.price > group.cheapest_price:
            ratio = candidate.price / group.cheapest_price
            pros.append(
                f"аналог по действующему веществу: {group.cheapest_name} — "
                f"в {ratio:.1f} раз дешевле"
            )
    return list(dict.fromkeys(pros))  # dedupe, keep order (multi-substance candidates)


def ingredient_cons(cons: list[str], candidate: Candidate, ctx: IngredientContext) -> list[str]:
    """Swap the generic "weak ingredient" con for an honest composition con.

    Proven-absent candidates are hard-filtered out before scoring, so a
    "not present" candidate reaching here was either never checked or has no
    состав to read — flag that honestly, not as "weak" or "absent".
    """
    cons = [c for c in cons if c != "weak ingredient"]
    required = ctx.required_substances
    if not required or has_required_substance(candidate, required):
        return cons
    if not candidate.ingredients_checked:
        cons.append(f"состав не проверен: {', '.join(required)}?")
    elif not composition_text(candidate.specs):
        cons.append(f"состав не подтверждён: {', '.join(required)}?")
    return cons


# --- The capability ------------------------------------------------------------


def _book(ctx: CapabilityContext) -> IngredientBook:
    return IngredientBook.model_validate(ctx.params[INGREDIENT_KEY])


def _required(ctx: CapabilityContext) -> list[str]:
    return required_substances(_book(ctx), ctx.constraints)


class Composition(Capability):
    """The active substance decides: search, read, filter and score by it."""

    name = COMPOSITION
    title = "Состав"
    axis_name = "ingredient"
    # A cheap generic with the same substance and concentration is genuinely equivalent
    # to the pricey brand, so the substance leads and brand drops sharply.
    axis_weight = 25.0
    weight_shifts = {"brand": -15.0, "specs": -5.0, "popularity": -5.0}

    def setting(
        self, profile: ResolvedProfile, constraints: QueryConstraints
    ) -> CapabilitySetting | None:
        """On when the profile switches composition on with an ingredient book."""
        return profile.enabled(self.name) if profile.ingredient_book is not None else None

    def searches(self, ctx: CapabilityContext) -> list[tuple[str, list[str]]]:
        required = _required(ctx)
        return substance_search_plan(_book(ctx), required) if required else []

    def relevance(self, ctx: CapabilityContext, pool: list[Candidate]) -> set[str]:
        required = _required(ctx)
        return substance_confirmed_ids(pool, _book(ctx), required) if required else set()

    def hard_features(self, ctx: CapabilityContext, features: list[str]) -> list[str]:
        """A bare concentration ("0,1%") is graded on the ingredient axis, not string-matched."""
        if not _required(ctx):
            return features
        return [f for f in features if not CONCENTRATION_RE.fullmatch(f.strip())]

    async def enrich(self, ctx: CapabilityContext, pool: list[Candidate]) -> None:
        """Title check, then extraction; refuse a pool the substance filter would empty.

        Only checked candidates can be proven absent, so a failed extraction keeps the
        pool rather than raising ``NoSubstanceMatch``.
        """
        book, required = _book(ctx), _required(ctx)
        mark_title_substances(pool, book, required)
        await extract_ingredients_into(pool, ctx.constraints.search_phrase, book, ctx.use_llm)
        if required and ctx.hard_filters is not None:
            guard_substance_pool(pool, required, ctx.hard_filters)

    def reject_content(self, ctx: CapabilityContext, candidate: Candidate) -> str | None:
        required = _required(ctx)
        if required and substance_proven_absent(candidate, required):
            return f"{NO_SUBSTANCE_REASON}: {', '.join(required)}"
        return None

    def prepare(self, ctx: CapabilityContext, passed: list[Candidate]) -> IngredientContext:
        return build_ingredient_context(passed, _required(ctx))

    def axis(
        self, ctx: CapabilityContext, state: Any, candidate: Candidate, weights: dict[str, float]
    ) -> AxisScore:
        return score_ingredient(candidate, state, weights, required_concentration(ctx.constraints))

    def notes(
        self,
        ctx: CapabilityContext,
        state: Any,
        candidate: Candidate,
        pros: list[str],
        cons: list[str],
    ) -> tuple[list[str], list[str]]:
        return [*pros, *equivalence_verdicts(candidate, state)], ingredient_cons(
            cons, candidate, state
        )

    def section(self, ctx: CapabilityContext, result: Recommendation) -> Section:
        """Flag a ranked candidate whose composition was not checked."""
        unchecked = any(not s.candidate.ingredients_checked for s in result.ranked)
        return Section(flags={INGREDIENTS_UNCHECKED: unchecked})
