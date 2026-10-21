"""Category-relevance filtering: drop non-products before ranking.

Marketplace search relevance is loose — a "робот пылесос" query surfaces spare
batteries, nozzles, cases and unrelated items that then pollute the ranking
(live evidence: a Dyson *battery* ranked #1 for "робот пылесос"). Two passes,
both fail-open (a filter bug must never empty the pool):

1. ``accessory_reason`` — a free, pure heuristic that always runs (even
   ``--no-llm``): a title carrying an accessory marker (насадка / аккумулятор /
   чехол / …) *without* the category head-noun is almost always a part, not the
   product. Careful with markers that legitimately appear inside real products
   (a vacuum "с HEPA фильтром") — the marker only disqualifies when it opens the
   title or the category noun is missing entirely.
2. ``filter_relevant`` — one Jev request (TypeSafe System One via OmniRoute,
   ``llm/jev.py``): a yes/no Noul per surviving title, kept at
   ``noul >= KEEP_THRESHOLD``. Not cached: a call is ~0.5 s and ~$0.0001. When
   Jev fails, the previous batched ``free-stack`` LLM call (mandatory cache) runs instead;
   when that fails too, or returns an out-of-range index, it keeps everything.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from buysmarter.llm.adapter import LlmConfigError, LlmError, generate
from buysmarter.llm.jev import system_one

logger = logging.getLogger(__name__)

# Fallback when Jev fails: cheap, mechanical classification → the OmniRoute
# free-stack combo (what the old "haiku" alias resolved to).
RELEVANCE_MODEL = "free-stack"

# Keep a title at or above this P(real product). Recall-biased on purpose: the pool
# is fail-open (``_restore_min``), and a bare model code ("Tefal X-plorer Serie 75")
# scores ~0.3 while accessories score <= 0.24.
# ponytail: tuned on 26 hand-labelled titles (docs/jev-migration.md);
# retune after 50 real runs or at the first miss seen in one.
KEEP_THRESHOLD = 0.25
# OLX ads have no fail-open floor and come from a looser search: a neighbouring
# product type ("туфли" for "кроссовки", ~0.5) must go, "кеды" (~0.6) may stay.
# ponytail: tuned on one sneaker run; retune at the first wrong OLX listing.
USED_KEEP_THRESHOLD = 0.55

# Noul criteria shared by every title question (Jev reads instructions literally,
# so what counts as "no" is spelled out).
_RELEVANCE_CRITERIA = {
    "true": "A standalone product of the category `category` that the shopper would buy "
    "as the main item",
    "false": "An accessory, spare part, consumable, nozzle, battery, charger, case, filter, "
    "a different product type, or an unrelated item",
}

# Accessory / spare-part / consumable markers (lowercased stems). Russian stems
# are trimmed to the shortest form that still matches every declension we care
# about — e.g. "насад" catches насадка/насадки/насадок (the genitive-plural
# fill-vowel breaks a longer "насадк" stem).
_ACCESSORY_MARKERS: tuple[str, ...] = (
    "насад",  # насадка/насадки/насадок (nozzles/tips)
    "аккумулятор",
    "батаре",  # батарея/батареи
    "чехол",
    "фильтр",  # only fires at title start or when category noun absent (see below)
    "запчаст",
    "запасн",
    "зарядн",  # зарядное/зарядка
    "кабель",
    "крепле",  # крепление
    "adapter",
    "battery",
    "spare",
    "nozzle",
    "tips",
    "case",
    "filter",
    "charger",
)

# Shortest category token treated as a real head-noun (skips stray short words).
_CATEGORY_TOKEN_MIN = 4


class RelevanceResult(BaseModel):
    """LLM fallback verdict: the indices (0-based) of titles that are real products."""

    relevant_indices: list[int] = Field(default_factory=list)


def _category_head_noun(category: str) -> str | None:
    """Last meaningful token of the category (its head-noun), or None."""
    words = re.findall(r"[^\W\d_]+", category.lower())
    tokens = [t for t in words if len(t) >= _CATEGORY_TOKEN_MIN]
    return tokens[-1] if tokens else None


def _first_word(title_lower: str) -> str:
    """First non-numeric word of a title (skips leading quantities like "5 ")."""
    for raw in title_lower.split():
        word = re.sub(r"[^\w]", "", raw, flags=re.UNICODE)
        if word and not word.isdigit():
            return word
    return ""


def _marker_at_word_start(marker: str, text: str) -> bool:
    """True if a Latin ``marker`` starts a word ("showcase" ≠ "case"); Cyrillic stems match
    anywhere so compounds ("микрофильтр", "подзарядное") still count; suffixes stay free."""
    if not marker.isascii():
        return marker in text
    return re.search(rf"(?<!\w){re.escape(marker)}", text) is not None


def accessory_reason(category: str, title: str) -> str | None:
    """Return the accessory marker disqualifying ``title``, or None if it looks real.

    Pure. A marker disqualifies only when it *opens* the title (an accessory
    listing — "Аккумулятор для…", "5 насадок для…") or the category head-noun is
    absent from the title, so a real product that merely mentions a part ("робот
    пылесос с HEPA фильтром") is kept.
    """
    lowered = title.lower()
    marker = next((m for m in _ACCESSORY_MARKERS if _marker_at_word_start(m, lowered)), None)
    if marker is None:
        return None
    head = _category_head_noun(category)
    starts_with_marker = _first_word(lowered).startswith(marker)
    category_absent = head is not None and head not in lowered
    return marker if (starts_with_marker or category_absent) else None


def relevance_questions(count: int) -> dict[str, dict[str, Any]]:
    """One Noul per title, keyed ``t<i>`` and pointing at ``titles[i]`` in the state."""
    return {
        f"t{i}": {
            "type": "noul",
            "instructions": f"Is `titles[{i}]` an actual product of the category `category`?",
            "criteria": _RELEVANCE_CRITERIA,
        }
        for i in range(count)
    }


def filter_relevant_prompt(category: str, titles: list[str]) -> str:
    """Build the LLM fallback's batched classification prompt over numbered titles."""
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
    return (
        f'Category the shopper is searching for: "{category}".\n\n'
        "Below is a numbered list of marketplace search results. Return the "
        "indices of the ones that are ACTUAL standalone products of this "
        "category. EXCLUDE accessories, spare parts, consumables, nozzles, "
        "batteries, chargers, cases, filters and anything unrelated to the "
        'category. Return JSON: {"relevant_indices": [<index>, ...]} using the '
        "numbers shown.\n\n"
        f"{numbered}"
    )


async def _filter_relevant_llm(category: str, titles: list[str]) -> list[bool]:
    """The previous LLM pass: one batched, cached call; fail-open to all-True."""
    try:
        result = await generate(
            filter_relevant_prompt(category, titles), RelevanceResult, model=RELEVANCE_MODEL
        )
    except (LlmError, LlmConfigError) as exc:
        logger.warning("relevance LLM call failed (%r); keeping all %d titles", exc, len(titles))
        return [True] * len(titles)
    indices = {i for i in result.relevant_indices}  # type: ignore[attr-defined]
    if any(i < 0 or i >= len(titles) for i in indices):
        logger.warning(
            "relevance LLM returned out-of-range indices %s for %d titles; keeping all",
            sorted(indices),
            len(titles),
        )
        return [True] * len(titles)
    return [i in indices for i in range(len(titles))]


async def filter_relevant(
    category: str, titles: list[str], keep_threshold: float = KEEP_THRESHOLD
) -> list[bool]:
    """Classify each title as a real product of ``category`` (True) or not.

    One Jev request, all questions answered in parallel over the same state.
    Any Jev failure falls back to the LLM pass, itself fail-open, so a
    classifier hiccup never drops legitimate products.
    """
    if not titles:
        return []
    try:
        answers = await system_one(
            {"category": category, "titles": titles}, relevance_questions(len(titles))
        )
        return [answers[f"t{i}"]["noul"] >= keep_threshold for i in range(len(titles))]
    except (LlmError, LlmConfigError, KeyError, TypeError) as exc:
        logger.warning("relevance Jev call failed (%r); falling back to the LLM", exc)
        return await _filter_relevant_llm(category, titles)
