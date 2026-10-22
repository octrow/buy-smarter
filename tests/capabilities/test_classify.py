"""The Jev classifier: category capabilities for a new product type (phase 3, decisions 6-7).

One Jev call per new category, shared with the related-profile pick; "yes" and "no" are
both saved; a failed call saves nothing; a second request asks nothing; no LLM, no Jev.
"""

from typing import Any

import pytest

from buysmarter.capabilities import classify
from buysmarter.capabilities.classify import capability_questions, ensure_profile
from buysmarter.knowledge import store as store_mod
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.models_book import ModelBook
from buysmarter.knowledge.store import load_knowledge, save_knowledge
from buysmarter.llm.adapter import LlmError


class _Jev:
    """Answers every capability question with ``noul`` and the match with ``choice``."""

    def __init__(self, noul: dict[str, float], choice: str = "none of these") -> None:
        self.noul, self.choice = noul, choice
        self.calls: list[set[str]] = []

    async def __call__(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(set(questions))
        assert questions.keys() == {"match"} or state["product_type"]
        answers: dict[str, Any] = {
            key: {"noul": self.noul.get(key, 0.1)} for key in questions if key != "match"
        }
        if "match" in questions:
            answers["match"] = {"choice": self.choice, "confidence": 0.95}
        return answers


@pytest.fixture(autouse=True)
def _classifier_on(monkeypatch) -> None:
    """tests/conftest.py switches the classifier off for the rest of the suite."""
    monkeypatch.setattr(classify, "capability_questions", capability_questions)


def _fake(monkeypatch, jev: _Jev) -> _Jev:
    monkeypatch.setattr(store_mod, "system_one", jev)
    monkeypatch.setattr(classify, "system_one", jev)
    return jev


async def _caps(slug: str) -> dict[str, Any]:
    return (await load_knowledge(slug) or {})["capabilities"]


async def test_a_new_category_in_an_empty_store_is_classified_in_one_call(monkeypatch) -> None:
    jev = _fake(monkeypatch, _Jev({"cap_composition": 0.9, "bench_kimovil": 0.05}))

    profile = await ensure_profile("шампунь", use_llm=True)

    assert jev.calls == [set(capability_questions())]
    caps = await _caps("шампунь")
    assert caps["composition"] == {"enabled": True, "params": {}, "set_by": "jev", "user": None}
    assert caps["benchmark"]["enabled"] is False and caps["benchmark"]["set_by"] == "jev"
    assert profile.capabilities["composition"].set_by == "jev"
    assert profile.ingredient_book is None  # no book yet: composition is not active


async def test_the_questions_ride_in_the_related_profile_pick(monkeypatch) -> None:
    await save_knowledge("пылесос", BrandBook(), ModelBook(), "opus")
    jev = _fake(monkeypatch, _Jev({"bench_kimovil": 0.97}))

    await ensure_profile("смартфон", use_llm=True)

    assert jev.calls == [{"match", *capability_questions()}]
    caps = await _caps("смартфон")
    assert caps["benchmark"]["params"] == {"provider": "kimovil"}
    assert caps["composition"]["enabled"] is False


async def test_a_found_related_profile_drops_the_answers(monkeypatch) -> None:
    await save_knowledge("косметика", BrandBook(), ModelBook(), "opus")
    _fake(monkeypatch, _Jev({"cap_composition": 0.9}, choice="косметика"))

    profile = await ensure_profile("крем", use_llm=True)

    assert profile.found == "косметика"
    assert await load_knowledge("крем") is None


async def test_a_second_request_asks_the_classifier_nothing(monkeypatch) -> None:
    jev = _fake(monkeypatch, _Jev({"cap_composition": 0.9}))
    await ensure_profile("шампунь", use_llm=True)
    await save_knowledge("пылесос", BrandBook(), ModelBook(), "opus")

    await ensure_profile("шампунь", use_llm=True)

    assert jev.calls[1:] == [{"match"}]


async def test_a_failed_call_saves_nothing(monkeypatch) -> None:
    async def _down(state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        raise LlmError("Jev down")

    monkeypatch.setattr(classify, "system_one", _down)

    await ensure_profile("шампунь", use_llm=True)

    assert await load_knowledge("шампунь") is None


async def test_a_failed_shared_call_saves_nothing(monkeypatch) -> None:
    await save_knowledge("пылесос", BrandBook(), ModelBook(), "opus")

    async def _down(state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        raise LlmError("Jev down")

    monkeypatch.setattr(store_mod, "system_one", _down)
    monkeypatch.setattr(classify, "system_one", _down)

    await ensure_profile("шампунь", use_llm=True)

    assert await load_knowledge("шампунь") is None


async def test_without_an_llm_jev_is_never_called(monkeypatch) -> None:
    jev = _fake(monkeypatch, _Jev({}))

    await ensure_profile("шампунь", use_llm=False)

    assert jev.calls == []
    assert await load_knowledge("шампунь") is None


def test_only_category_capabilities_with_a_question_are_asked() -> None:
    assert set(capability_questions()) == {"cap_composition", "bench_kimovil"}
