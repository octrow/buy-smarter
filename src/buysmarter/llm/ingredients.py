"""Per-candidate active-ingredient extraction for composition-driven categories.

For cosmetics/pharmacy, ranking needs to know which active substance (and at
what concentration) each candidate actually contains — the Клензит-vs-Differin
equivalence is invisible from brand/price alone (docs/PLAN.md "Composition-driven
категории").

Jev first (one request, ``llm/jev.py``): a Noul per (candidate, book substance)
— "does this title + состав contain it" — and, where code finds percentages in
the text, a Choice that *selects* the active's concentration among them (copied
verbatim, never generated). Jev only knows the book's substances, so an empty
book, or any Jev failure, falls back to the previous batched, cached ``free-stack``
LLM call, which can also name substances outside the book.

Fail-open: any failure degrades to "no ingredients detected" for the affected
candidate, never raising — extraction only enriches ranking.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from buysmarter.knowledge.ingredients import IngredientBook
from buysmarter.llm.adapter import LlmConfigError, LlmError, generate
from buysmarter.llm.jev import system_one

logger = logging.getLogger(__name__)

# Fallback when Jev can't answer: cheap, mechanical structured pull → the OmniRoute
# free-stack combo (what the old "haiku" alias resolved to).
EXTRACT_INGREDIENTS_MODEL = "free-stack"

# ponytail: thresholds tuned on a hand-labelled acne-care set (docs/jev-migration.md §4).
SUBSTANCE_MIN_NOUL = 0.5
CONCENTRATION_MIN_PROB = 0.5
# Concentration candidates: "0,1%", "0.3 %", "5%".
_PERCENT_RE = re.compile(r"\d+(?:[.,]\d+)?\s*%")
_NO_CONCENTRATION = "none of these"


class DetectedIngredients(BaseModel):
    """One candidate's verdict: canonical substances found + concentration (or null)."""

    index: int
    substances: list[str] = Field(default_factory=list)
    concentration: str | None = None


class IngredientExtractionResult(BaseModel):
    """LLM response: one detection per (0-based) candidate index."""

    detections: list[DetectedIngredients] = Field(default_factory=list)


def extract_ingredients_prompt(
    category: str, book: IngredientBook, items: list[tuple[str, str]]
) -> str:
    """Build the batched extraction prompt over numbered (title, состав) items."""
    known = ", ".join(s.canonical for s in book.substances) or "(none listed)"
    numbered = "\n".join(
        f"{i}. title: {title}\n   состав/specs: {sostav or '(none)'}"
        for i, (title, sostav) in enumerate(items)
    )
    return (
        f'Category: "{category}". Known canonical active substances for this '
        f"category: {known}.\n\n"
        "Below is a numbered list of marketplace products with their title and "
        "composition (состав) text. For each item detect the ACTIVE substance(s) "
        "it contains and the concentration if stated. Map each substance to one "
        "of the known canonical names above when it matches (translate/normalize "
        "spellings, e.g. adapalene -> адапален). Return the concentration exactly "
        'as written (e.g. "0,1%", "0.3%") or null if absent.\n\n'
        'Return JSON: {"detections": [{"index": <int>, "substances": [<canonical '
        'name>, ...], "concentration": <string or null>}, ...]}, one per item.\n\n'
        f"{numbered}"
    )


def _percent_candidates(title: str, sostav: str) -> list[str]:
    """Distinct percentages written in the title or состав, as written."""
    return list(dict.fromkeys(m.strip() for m in _PERCENT_RE.findall(f"{title} {sostav}")))


def build_ingredient_questions(
    category: str, book: IngredientBook, items: list[tuple[str, str]]
) -> tuple[dict, dict[str, dict], list[list[str]]]:
    """State, questions, and each item's concentration candidates (behind ``c<i>``)."""
    state = {
        "category": category,
        "items": [{"title": t, "composition": s or "(none)"} for t, s in items],
        "substances": [{"name": x.canonical, "aliases": x.aliases} for x in book.substances],
    }
    questions: dict[str, dict] = {}
    percents = [_percent_candidates(t, s) for t, s in items]
    for i in range(len(items)):
        for j in range(len(book.substances)):
            questions[f"i{i}s{j}"] = {
                "type": "noul",
                "instructions": f"Does the product `items[{i}]` contain the active substance "
                f"`substances[{j}].name` (any spelling, translation or alias in "
                f"`substances[{j}].aliases`) according to its title or composition?",
            }
        if percents[i]:
            criteria: dict[str, str | None] = dict.fromkeys(percents[i])
            criteria[_NO_CONCENTRATION] = "None of these is the active substance's concentration"
            questions[f"c{i}"] = {
                "type": "choice",
                "instructions": f"Which percentage in `items[{i}]` is the concentration of its "
                "active substance (not SPF, not a discount, not another ingredient's share)?",
                "criteria": criteria,
            }
    return state, questions, percents


async def _extract_jev(
    category: str, book: IngredientBook, items: list[tuple[str, str]]
) -> list[DetectedIngredients]:
    """Jev detection over the book's substances. Raises on any failure (caller falls back)."""
    if not book.substances:
        raise LlmConfigError("empty IngredientBook: nothing for Jev to check")
    state, questions, percents = build_ingredient_questions(category, book, items)
    answers = await system_one(state, questions)
    detections = []
    for i in range(len(items)):
        substances = [
            x.canonical
            for j, x in enumerate(book.substances)
            if answers[f"i{i}s{j}"]["noul"] >= SUBSTANCE_MIN_NOUL
        ]
        concentration = None
        if substances and percents[i]:
            pick = answers[f"c{i}"]
            if pick["choice"] != _NO_CONCENTRATION and (
                pick["probabilities"][pick["choice"]] >= CONCENTRATION_MIN_PROB
            ):
                concentration = pick["choice"]
        detections.append(
            DetectedIngredients(index=i, substances=substances, concentration=concentration)
        )
    return detections


async def _extract_llm(
    category: str, book: IngredientBook, items: list[tuple[str, str]]
) -> list[DetectedIngredients]:
    """The previous batched, cached LLM pull; fail-open to empty detections."""
    empty = [DetectedIngredients(index=i) for i in range(len(items))]
    try:
        result = await generate(
            extract_ingredients_prompt(category, book, items),
            IngredientExtractionResult,
            model=EXTRACT_INGREDIENTS_MODEL,
        )
    except (LlmError, LlmConfigError) as exc:
        logger.warning("ingredient extraction failed (%r); no substances detected", exc)
        return empty

    detections = empty
    for det in result.detections:  # type: ignore[attr-defined]
        if 0 <= det.index < len(items):
            detections[det.index] = det
    return detections


async def extract_ingredients(
    category: str, book: IngredientBook, items: list[tuple[str, str]]
) -> list[DetectedIngredients]:
    """Extract active substances + concentration per candidate (Jev, LLM fallback).

    ``items`` is ``[(title, состав_text), ...]``. Returns one ``DetectedIngredients``
    per item (same order/length), empty for any item nothing was found for.
    """
    if not items:
        return []
    try:
        return await _extract_jev(category, book, items)
    except (LlmError, LlmConfigError, KeyError, TypeError) as exc:
        logger.warning("ingredient Jev call failed (%r); falling back to the LLM", exc)
        return await _extract_llm(category, book, items)
