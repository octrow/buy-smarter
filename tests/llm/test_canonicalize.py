"""Tests for LLM canonicalization of mangled OLX titles (stubbed generate)."""

from buysmarter.llm import canonicalize as canon
from buysmarter.llm.canonicalize import (
    CANONICALIZE_MODEL,
    CanonicalizeResult,
    CanonicalMapping,
    canonicalize_prompt,
    canonicalize_titles,
)


def test_model_choice_is_free_stack() -> None:
    assert CANONICALIZE_MODEL == "free-stack"


def test_prompt_lists_known_models_and_numbers_titles() -> None:
    prompt = canonicalize_prompt(
        "ирригатор", ["Xiaomi F300"], ["Ирригатор xiaomi me 705", "непонятная вещь"]
    )
    assert "Xiaomi F300" in prompt
    assert "0. Ирригатор xiaomi me 705" in prompt
    assert "1. непонятная вещь" in prompt
    assert "ирригатор" in prompt


async def test_no_titles_skips_llm_call(monkeypatch) -> None:
    called = False

    async def _fake_generate(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("should not be called")

    monkeypatch.setattr(canon, "generate", _fake_generate)

    result = await canonicalize_titles("ирригатор", ["Xiaomi F300"], [])
    assert result == []
    assert called is False


async def test_maps_to_existing_known_model(monkeypatch) -> None:
    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        assert model == "free-stack"
        return CanonicalizeResult(
            mappings=[CanonicalMapping(index=0, canonical="Xiaomi F300", is_new=False)]
        )

    monkeypatch.setattr(canon, "generate", _fake_generate)

    result = await canonicalize_titles("ирригатор", ["Xiaomi F300"], ["Ирригатор ксиоми ф300"])
    assert result == ["Xiaomi F300"]


async def test_new_canonical_suggestion_returned(monkeypatch) -> None:
    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return CanonicalizeResult(
            mappings=[
                CanonicalMapping(index=0, canonical="Xiaomi Water Flosser 1 (MEO701)", is_new=True)
            ]
        )

    monkeypatch.setattr(canon, "generate", _fake_generate)

    result = await canonicalize_titles(
        "ирригатор", ["Xiaomi F300"], ["Xiaomi ирригатор Mijia Electric Flusher MEO701"]
    )
    assert result == ["Xiaomi Water Flosser 1 (MEO701)"]


async def test_null_when_unidentifiable(monkeypatch) -> None:
    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return CanonicalizeResult(mappings=[CanonicalMapping(index=0, canonical=None)])

    monkeypatch.setattr(canon, "generate", _fake_generate)

    result = await canonicalize_titles("ирригатор", ["Xiaomi F300"], ["непонятная вещь"])
    assert result == [None]


async def test_fail_open_on_llm_error(monkeypatch) -> None:
    async def _fake_generate(*args, **kwargs):
        raise canon.LlmError("boom")

    monkeypatch.setattr(canon, "generate", _fake_generate)

    result = await canonicalize_titles("ирригатор", ["Xiaomi F300"], ["a", "b"])
    assert result == [None, None]


async def test_out_of_range_index_ignored(monkeypatch) -> None:
    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return CanonicalizeResult(mappings=[CanonicalMapping(index=5, canonical="Xiaomi F300")])

    monkeypatch.setattr(canon, "generate", _fake_generate)

    result = await canonicalize_titles("ирригатор", ["Xiaomi F300"], ["a"])
    assert result == [None]


# --- Jev picks known models; LLM handles the rest -------------------------


def _jev_picks(*picks: tuple[str, float]):
    async def _fake_system_one(state, questions):
        return {
            f"t{i}": {"type": "choice", "choice": choice, "confidence": conf}
            for i, (choice, conf) in enumerate(picks)
        }

    return _fake_system_one


def test_jev_question_offers_known_models_plus_no_match() -> None:
    questions = canon.canonicalize_questions(["Xiaomi F300", "Revyline RL 450"], 2)
    assert list(questions) == ["t0", "t1"]
    assert "`titles[1]`" in questions["t1"]["instructions"]
    assert list(questions["t0"]["criteria"]) == ["Xiaomi F300", "Revyline RL 450", canon._NO_MATCH]


async def test_confident_jev_matches_skip_llm(monkeypatch) -> None:
    async def _llm_must_not_run(*args, **kwargs):
        raise AssertionError("LLM ran although Jev matched every title")

    monkeypatch.setattr(canon, "system_one", _jev_picks(("Xiaomi F300", 0.93)))
    monkeypatch.setattr(canon, "generate", _llm_must_not_run)

    assert await canonicalize_titles("ирригатор", ["Xiaomi F300"], ["ксиоми ф300"]) == [
        "Xiaomi F300"
    ]


async def test_unsure_and_no_match_titles_stay_unresolved_without_the_llm(monkeypatch) -> None:
    async def _llm_must_not_run(*args, **kwargs):
        raise AssertionError("LLM ran although Jev answered")

    monkeypatch.setattr(
        canon,
        "system_one",
        _jev_picks(("Xiaomi F300", 0.95), (canon._NO_MATCH, 0.9), ("Xiaomi F300", 0.5)),
    )
    monkeypatch.setattr(canon, "generate", _llm_must_not_run)

    result = await canonicalize_titles(
        "ирригатор", ["Xiaomi F300"], ["ксиоми ф300", "Mijia MEO701", "Ирригатор Xiaomi"]
    )

    assert result == ["Xiaomi F300", None, None]


async def test_no_known_models_makes_no_call(monkeypatch) -> None:
    async def _must_not_run(*args, **kwargs):
        raise AssertionError("a model ran with nothing to pick from")

    monkeypatch.setattr(canon, "system_one", _must_not_run)
    monkeypatch.setattr(canon, "generate", _must_not_run)

    assert await canonicalize_titles("ирригатор", [], ["Mijia MEO701"]) == [None]
