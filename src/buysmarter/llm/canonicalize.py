"""LLM canonicalization of mangled OLX titles (docs/PLAN.md "ModelBook", phase 4).

Private OLX sellers write sloppy, inconsistent titles ("Ирригатор xiaomi me
705", "Xiaomi ирригатор Mijia Electric Flusher MEO701"). ``ModelBook.resolve``
only catches these when an explicit code/alias already exists; anything else
falls through to the weak brand-gate or is dropped entirely. This module fills
that gap: for every title the static book missed, match it to a known canonical
model, propose a brand-new canonical when the title clearly names a real model
absent from the book, or give up (``None``) when it's unidentifiable.

Matching is a Jev Choice per title (known models + "none of these", one request);
a pick at ``MATCH_MIN_CONFIDENCE`` or above is taken. Everything else — Jev
unsure, Jev saying "none", or any Jev failure — goes to the previous batched,
cached LLM call, which can also *generate* a new canonical name. After a
confident Jev "none", the LLM may only suggest a new name, never a known model.

Callers decide what to do with each verdict: a canonical that IS one of the
``known_models`` is the strongest signal (equivalent to a resolve hit) and its
alias should be learned into the book; a canonical that is NOT among
``known_models`` is a new-model suggestion for display only — it must never be
treated as a match to a different, existing model.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from buysmarter.llm.adapter import LlmConfigError, LlmError, generate
from buysmarter.llm.jev import system_one

logger = logging.getLogger(__name__)

# Mapping a mangled title to a known name (or proposing one) is a cheap,
# mechanical lookup task — the cheapest model, a conscious choice per the
# model policy documented in llm/adapter.py.
CANONICALIZE_MODEL = "free-stack"

# A Jev pick becomes a learned alias (persisted), so the bar is high.
# ponytail: tuned on 13 hand-labelled irrigator titles (matches >= 0.87);
# retune after 50 real runs or at the first miss seen in one.
MATCH_MIN_CONFIDENCE = 0.8
_NO_MATCH = "none of these"
_MAX_CHOICE_OPTIONS = 255  # Jev's Choice limit, "none of these" included


class CanonicalMapping(BaseModel):
    """One title's verdict: matched canonical, a new suggestion, or unidentifiable."""

    index: int
    canonical: str | None = None
    is_new: bool = False


class CanonicalizeResult(BaseModel):
    """LLM response: a mapping per (0-based) title index."""

    mappings: list[CanonicalMapping] = Field(default_factory=list)


def canonicalize_prompt(category: str, known_models: list[str], titles: list[str]) -> str:
    """Build the batched canonicalization prompt over numbered unresolved titles."""
    known = "\n".join(f"- {m}" for m in known_models) if known_models else "(none yet)"
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
    return (
        f'Category: "{category}".\n\n'
        "Known canonical product models for this category:\n"
        f"{known}\n\n"
        "Below is a numbered list of marketplace listing titles (often mangled "
        "by private sellers — typos, transliteration, missing spaces). For each "
        "title, return a mapping with:\n"
        '- "index": the number shown\n'
        '- "canonical": one of the known models above EXACTLY as given if the '
        "title clearly identifies it; otherwise, if the title clearly identifies "
        "a real, specific product model that is simply absent from the known "
        'list, a NEW canonical name in the form "Brand Model" (e.g. a title '
        'mentioning "MEO701" could become "Xiaomi Water Flosser 1 (MEO701)"); '
        "otherwise null if the title cannot be confidently identified\n"
        '- "is_new": true only when you proposed a brand-new canonical not in '
        "the known list, false otherwise\n\n"
        'Return JSON: {"mappings": [{"index": <int>, "canonical": <string or '
        'null>, "is_new": <bool>}, ...]}, one entry per title.\n\n'
        f"{numbered}"
    )


def canonicalize_questions(known_models: list[str], count: int) -> dict[str, dict[str, Any]]:
    """One Jev Choice per title (``t<i>`` → ``titles[i]``) over known models + no-match."""
    criteria: dict[str, str | None] = dict.fromkeys(known_models)
    criteria[_NO_MATCH] = (
        "A different model or version, or the title does not identify one specific model"
    )
    return {
        f"t{i}": {
            "type": "choice",
            "instructions": f"Which known product model is the listing `titles[{i}]`? It is a "
            "mangled marketplace title (typos, transliteration, Cyrillic spelling of Latin "
            "names). Pick a model only if the title names that exact model; a different "
            "model number or version is not a match.",
            "criteria": criteria,
        }
        for i in range(count)
    }


async def _canonicalize_jev(
    category: str, known_models: list[str], titles: list[str]
) -> list[tuple[str | None, bool]]:
    """Per title ``(known_match_or_None, confident)``. Raises on any Jev failure."""
    if not known_models or len(known_models) >= _MAX_CHOICE_OPTIONS:
        raise LlmConfigError(f"{len(known_models)} known models: not a Jev Choice")
    answers = await system_one(
        {"category": category, "titles": titles},
        canonicalize_questions(known_models, len(titles)),
    )
    picks = []
    for i in range(len(titles)):
        answer = answers[f"t{i}"]
        match = answer["choice"] if answer["choice"] in known_models else None
        picks.append((match, answer["confidence"] >= MATCH_MIN_CONFIDENCE))
    return picks


async def _canonicalize_llm(
    category: str, known_models: list[str], titles: list[str]
) -> list[str | None]:
    """The previous batched, cached LLM call; fail-open to all-``None``."""
    try:
        result = await generate(
            canonicalize_prompt(category, known_models, titles),
            CanonicalizeResult,
            model=CANONICALIZE_MODEL,
        )
    except (LlmError, LlmConfigError) as exc:
        logger.warning(
            "canonicalize LLM call failed (%s); leaving %d titles unresolved", exc, len(titles)
        )
        return [None] * len(titles)

    canonicals: list[str | None] = [None] * len(titles)
    for mapping in result.mappings:  # type: ignore[attr-defined]
        if 0 <= mapping.index < len(titles):
            canonicals[mapping.index] = mapping.canonical
    return canonicals


async def canonicalize_titles(
    category: str, known_models: list[str], titles: list[str]
) -> list[str | None]:
    """Resolve each unresolved ``titles`` entry to a canonical name, or ``None``.

    Jev's confident known-model picks stand; every other title goes to the LLM in
    one batch. Fail-open: no titles, or every call failing, leaves ``None`` —
    canonicalization is a nice-to-have display/learning aid, never a hard
    dependency of matching.
    """
    if not titles:
        return []
    try:
        picks = await _canonicalize_jev(category, known_models, titles)
    except (LlmError, LlmConfigError, KeyError, TypeError) as exc:
        logger.warning("canonicalize Jev call failed (%r); falling back to the LLM", exc)
        return await _canonicalize_llm(category, known_models, titles)

    rest = [i for i, (match, sure) in enumerate(picks) if not (match and sure)]
    if not rest:
        return [match if sure else None for match, sure in picks]
    suggested = await _canonicalize_llm(category, known_models, [titles[i] for i in rest])
    return _merge_picks(picks, dict(zip(rest, suggested, strict=True)), known_models)


def _merge_picks(
    picks: list[tuple[str | None, bool]],
    suggested: dict[int, str | None],
    known_models: list[str],
) -> list[str | None]:
    """Jev's confident picks, the LLM's suggestion for the rest.

    A title Jev was sure matches no known model keeps ``None`` when the LLM
    names a known model anyway (Jev's veto).
    """
    canonicals: list[str | None] = [match if sure else None for match, sure in picks]
    for i, canonical in suggested.items():
        vetoed = picks[i][1] and canonical in known_models
        canonicals[i] = None if vetoed else canonical
    return canonicals
