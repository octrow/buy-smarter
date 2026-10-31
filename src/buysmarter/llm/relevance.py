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
# OLX ads have no fail-open floor and come from a looser search, so they need a stricter cut.
# At 0.55 it dropped 16 of 162 real ads (12 used iPhone 13 at 0.23-0.54) to stop 3 wrong ones;
# 0.35 drops 5 and lets 6 through ("Мужские туфли Skechers" at 0.47 for "мужские кроссовки").
# ponytail: tuned on 14 queries labelled 2026-10-22 (relevance score log lines);
# retune when the user's labels land or at the first wrong OLX listing.
USED_KEEP_THRESHOLD = 0.35

# Noul criteria shared by every title question (Jev reads instructions literally,
# so what counts as "no" is spelled out). Every word of the category is a required
# property: without that Jev kept over-ear and wired headphones for "tws наушники".
# On 514 labelled titles (5 runs each) this cut false keeps 16.4 -> 10.8 per run
# (TWS categories 3.4 -> 0) for 1.2 false drops (Chiruca "полуботинки" for "мужские
# кроссовки", a "портативный … стационарный" irrigator).
# ponytail: generic wording, no per-category word lists; add them only if a category's
# form-factor words are still ignored in the relevance score log.
_RELEVANCE_CRITERIA = {
    "true": "A standalone product of the category `category` that the shopper would buy "
    "as the main item; it has every property the words of `category` name (type, form factor, "
    "connection, purpose)",
    "false": "An accessory, spare part, consumable, nozzle, battery, charger, case, filter "
    "(unless `category` itself is that kind of item, e.g. a case when it says чехол), "
    "a different product type, an unrelated item, or a product that lacks or contradicts a "
    "property named in `category` (wired when it says wireless, over-ear when it says in-ear, "
    "another model number when it names one)",
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
    "чехл",  # чехлы: the plural drops the vowel, so "чехол" misses it (same kind, below)
    "фильтр",  # only fires at title start or when category noun absent (see below)
    "запчаст",
    "запасн",
    "заряд",  # зарядное/зарядка/подзарядка
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

# Stems of one accessory kind: a category carrying any of them wants that kind ("чехол"
# also wants "Чехлы …"). A shorter shared stem ("чех") would also catch "Чехов".
_SAME_KIND: tuple[frozenset[str], ...] = (frozenset({"чехол", "чехл"}),)

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


def _wanted_markers(category_lower: str) -> set[str]:
    """Markers the category itself carries ("чехол", "зарядка"): what the shopper wants, kept."""
    carried = {m for m in _ACCESSORY_MARKERS if _marker_at_word_start(m, category_lower)}
    return carried.union(*(kind for kind in _SAME_KIND if kind & carried))


_FOR = re.compile(r"(?<!\w)для(?!\w)")
_STEM_TAIL = 3  # letters an inflection may change: "посудомойка" / "посудомоечных"
FOR_THE_CATEGORY = "для"  # the drop reason; such goods are never restored to fill the pool


def _product_stems(category: str) -> list[str]:
    """Stems of the goods' own words: before the category's "для" ("корм для кошек": корм)."""
    lowered = category.lower()
    goods = lowered[: m.start()] if (m := _FOR.search(lowered)) else lowered
    words = [w for w in re.findall(r"[^\W\d_]+", goods) if len(w) >= _CATEGORY_TOKEN_MIN]
    return [w[: max(_CATEGORY_TOKEN_MIN, len(w) - _STEM_TAIL)] for w in words]


def for_the_category(category: str, text: str) -> bool:
    """``text`` (a title or a store's leaf category) names goods FOR the category, not it.

    "Таблетки для посудомоечных машин" for "посудомойка": the goods' word comes only after
    "для". Keyless: without AI a colloquial query finds only such consumables on Kaspi.
    """
    lowered = text.lower()
    m = _FOR.search(lowered)
    if m is None:
        return False
    stems = _product_stems(category)
    before, after = lowered[: m.start()], lowered[m.end() :]
    return any(s in after for s in stems) and not any(s in before for s in stems)


def accessory_reason(category: str, title: str) -> str | None:
    """Return the accessory marker disqualifying ``title``, or None if it looks real.

    Pure. A marker disqualifies only when it *opens* the title (an accessory
    listing — "Аккумулятор для…", "5 насадок для…") or the category head-noun is
    absent from the title, so a real product that merely mentions a part ("робот
    пылесос с HEPA фильтром") is kept.
    """
    lowered = title.lower()
    wanted = _wanted_markers(category.lower())
    marker = next(
        (m for m in _ACCESSORY_MARKERS if m not in wanted and _marker_at_word_start(m, lowered)),
        None,
    )
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
        "batteries, chargers, cases, filters (unless the category itself is "
        "that kind of item) and anything unrelated to the "
        'category. Return JSON: {"relevant_indices": [<index>, ...]} using the '
        "numbers shown.\n\n"
        f"{numbered}"
    )


async def _filter_relevant_llm(category: str, titles: list[str], fail_open: bool) -> list[bool]:
    """The previous LLM pass: one batched, cached call; on failure all get ``fail_open``."""
    try:
        result = await generate(
            filter_relevant_prompt(category, titles), RelevanceResult, model=RELEVANCE_MODEL
        )
    except (LlmError, LlmConfigError) as exc:
        logger.warning(
            "relevance LLM call failed (%r); %s all %d titles",
            exc,
            "keeping" if fail_open else "dropping",
            len(titles),
        )
        return [fail_open] * len(titles)
    indices = {i for i in result.relevant_indices}  # type: ignore[attr-defined]
    if any(i < 0 or i >= len(titles) for i in indices):
        logger.warning(
            "relevance LLM returned out-of-range indices %s for %d titles; %s all",
            sorted(indices),
            len(titles),
            "keeping" if fail_open else "dropping",
        )
        return [fail_open] * len(titles)
    return [i in indices for i in range(len(titles))]


async def filter_relevant(
    category: str,
    titles: list[str],
    keep_threshold: float = KEEP_THRESHOLD,
    fail_open: bool = True,
) -> list[bool]:
    """Classify each title as a real product of ``category`` (True) or not.

    One Jev request, all questions answered in parallel over the same state.
    Any Jev failure falls back to the LLM pass. When that fails too, every title
    gets ``fail_open``: kept for a pool with a floor, dropped for OLX ads, where an
    empty section beats a wrong one.
    """
    if not titles:
        return []
    try:
        answers = await system_one(
            {"category": category, "titles": titles}, relevance_questions(len(titles))
        )
        scores = [answers[f"t{i}"]["noul"] for i in range(len(titles))]
        flags = [score >= keep_threshold for score in scores]
    except (LlmError, LlmConfigError, KeyError, TypeError) as exc:
        logger.warning("relevance Jev call failed (%r); falling back to the LLM", exc)
        return await _filter_relevant_llm(category, titles, fail_open)
    _log_scores(category, titles, scores, keep_threshold)
    return flags


def _log_scores(category: str, titles: list[str], scores: list[float], threshold: float) -> None:
    """One line per title: the labelled runs these build up are what the threshold is tuned on."""
    for title, score in zip(titles, scores, strict=True):
        verdict = "keep" if score >= threshold else "drop"
        logger.info(
            "relevance score: %.3f %s threshold=%.2f category=%r title=%r",
            score,
            verdict,
            threshold,
            category,
            title,
        )
