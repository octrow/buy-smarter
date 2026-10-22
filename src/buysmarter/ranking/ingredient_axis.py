"""The ingredient axis for composition-driven categories.

Scores the required active substance and its concentration, and names generic equivalents.
"""

from typing import TYPE_CHECKING

from buysmarter.knowledge.ingredients import composition_text, normalize_concentration
from buysmarter.ranking.filters import CONCENTRATION_RE, has_required_substance
from buysmarter.ranking.models import (
    AxisScore,
    Candidate,
    EquivalenceGroup,
    IngredientContext,
    QueryConstraints,
)

if TYPE_CHECKING:
    pass

# --- Ingredient axis (composition-driven categories, docs/PLAN.md) ---------
# Credit for the active-substance axis: required substance present with a matching
# (or simply known) concentration is full credit; present but unknown/mismatched
# concentration is a partial 0.7; absent is 0.
_INGREDIENT_PRESENT_UNKNOWN_CONC = 0.7
# Composition not checked (no extraction, or no answer for this candidate): the
# same honest partial credit for everyone, so the axis does not reorder an
# unchecked pool — a data gap is not proven absence.
_INGREDIENT_UNCHECKED_CREDIT = 0.3


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


def build_ingredient_context(
    candidates: list[Candidate], required_substances: list[str]
) -> IngredientContext:
    """Pool-wide equivalence groups for the ingredient axis (pure, docs/PLAN.md).

    Targets the query's required substances, falling back to every substance seen
    in the pool. A group is recorded only when two+ candidates share the substance
    at different prices — that price gap is what powers the generic-equivalence
    "аналог по действующему веществу" advice.
    """
    present = {s for c in candidates for s in c.detected_substances}
    targets = [s for s in required_substances if s in present] or sorted(present)
    groups = {s: g for s in targets if (g := _equivalence_group(candidates, s)) is not None}
    return IngredientContext(required_substances=required_substances, groups=groups)


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
