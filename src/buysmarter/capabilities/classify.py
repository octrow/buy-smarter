"""The Jev classifier: which category capabilities a new product type gets (phase 3, decisions 6-7).

Asked once per product type, only with an LLM, only while nothing is stored for it (no
profile with books and no record without books). The questions ride in the Jev call that
picks a related profile; when no such call happens (an empty store), they go alone. A
related profile found means the answers are dropped. Both "yes" and "no" are saved
(``set_by="jev"``), so a later fallback rule cannot switch on what Jev said no to; a
failed call saves nothing. The state is ``{"product_type": ...}``: the need belongs to
the request, not the category.
"""

import logging
from typing import Any

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.benchmark import BENCHMARK
from buysmarter.capabilities.benchmark_providers import BENCHMARK_PROVIDERS, PROVIDER_KEY
from buysmarter.knowledge.profile import (
    CAPABILITIES_KEY,
    PARENT_KEY,
    CapabilitySetting,
    ResolvedProfile,
    combine_all,
    has_books,
)
from buysmarter.knowledge.store import (
    Answers,
    Questions,
    load_knowledge,
    resolve_and_ask,
    update_knowledge,
)
from buysmarter.llm.adapter import LlmConfigError, LlmError
from buysmarter.llm.jev import system_one

logger = logging.getLogger(__name__)

# Measured 2026-10-23 (plan step 3): no ≤ 0.13, borderline "шампунь" 0.47-0.53 and
# "зубная паста" 0.58, yes ≥ 0.76 ("шампунь от перхоти", "витамины", "крем от акне");
# phones 0.96-0.98 against ≤ 0.04. The cut sits between borderline and yes: a borderline
# category gets composition from learning, not from Jev.
CLASSIFY_MIN_NOUL = 0.65
_WRITER = "jev"
_CAP = "cap_"
_BENCH = "bench_"


def _noul(question: str) -> dict[str, Any]:
    return {"type": "noul", "instructions": f"About the product type `product_type`: {question}"}


def capability_questions() -> Questions:
    """One noul per category capability that has a question, and one per benchmark provider."""
    questions = {
        f"{_CAP}{cap.name}": _noul(cap.jev_question)
        for cap in REGISTRY.values()
        if cap.level == "category" and cap.jev_question
    }
    for key, provider in BENCHMARK_PROVIDERS.items():
        questions[f"{_BENCH}{key}"] = _noul(provider.jev_question)
    return questions


def _yes(answers: Answers, key: str) -> bool:
    return float(answers[key]["noul"]) >= CLASSIFY_MIN_NOUL


def settings_from(answers: Answers) -> dict[str, CapabilitySetting]:
    """Jev's answers as capability settings: on or off for each question asked."""
    settings = {
        key.removeprefix(_CAP): CapabilitySetting(enabled=_yes(answers, key), set_by="jev")
        for key in answers
        if key.startswith(_CAP)
    }
    providers = [
        k.removeprefix(_BENCH) for k in answers if k.startswith(_BENCH) and _yes(answers, k)
    ]
    if any(k.startswith(_BENCH) for k in answers):
        params = {PROVIDER_KEY: providers[0]} if providers else {}
        settings[BENCHMARK] = CapabilitySetting(
            enabled=bool(providers), params=params, set_by="jev"
        )
    return settings


def _unclassified(stored: dict[str, Any] | None) -> bool:
    """Nothing stored that a lookup or an earlier classification left (learning marks aside)."""
    if stored is None:
        return True
    return not has_books(stored) and not stored.get(PARENT_KEY) and CAPABILITIES_KEY not in stored


async def _ask_alone(product_type: str, questions: Questions) -> Answers:
    """The questions in a call of their own; ``{}`` when Jev fails."""
    try:
        return await system_one({"product_type": product_type}, questions)
    except (LlmError, LlmConfigError) as exc:
        logger.warning("capability classifier Jev call failed (%r); nothing saved", exc)
        return {}


def _unasked(questions: Questions, capabilities: dict[str, CapabilitySetting]) -> Questions:
    """The questions about capabilities the profile has no setting for (added after it)."""
    return {
        key: q
        for key, q in questions.items()
        if (BENCHMARK if key.startswith(_BENCH) else key.removeprefix(_CAP)) not in capabilities
    }


async def _save(category: str, profile: ResolvedProfile, answers: Answers) -> ResolvedProfile:
    """Jev's answers saved under ``category`` and laid on ``profile``; nothing on a bad answer."""
    try:
        settings = settings_from(answers)
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("capability classifier answer malformed (%r); nothing saved", exc)
        return profile
    if not settings:
        return profile
    raw = {name: s.model_dump(mode="json") for name, s in settings.items()}
    await update_knowledge(category, {CAPABILITIES_KEY: raw}, _WRITER)
    logger.info(
        "capabilities classified: %s -> %s", category, {n: s.enabled for n, s in settings.items()}
    )
    capabilities = combine_all(profile.capabilities, settings)
    return profile.model_copy(update={"capabilities": capabilities})


async def ensure_profile(
    category: str, *, use_llm: bool, search_phrase: str | None = None
) -> ResolvedProfile:
    """``resolve_profile``, plus the classifier's capabilities for a product type seen first.

    A stored profile found is asked, once, only about capabilities added after it was
    classified or learned (style came after the shoe profiles).
    """
    stored = await load_knowledge(category) if use_llm else None
    questions = capability_questions() if use_llm and _unclassified(stored) else {}
    profile, answers = await resolve_and_ask(
        category, use_llm=use_llm, search_phrase=search_phrase, questions=questions
    )
    if profile.found is not None:
        unasked = _unasked(capability_questions(), profile.capabilities) if use_llm else {}
        if not unasked:
            return profile
        return await _save(profile.found, profile, await _ask_alone(profile.found, unasked))
    if not questions:
        return profile
    if answers is None:
        answers = await _ask_alone(category, questions)
    return await _save(category, profile, answers)
