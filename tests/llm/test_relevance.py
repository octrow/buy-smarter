"""Tests for the category-relevance filter: heuristic + Jev pass (stubbed)."""

from buysmarter.llm import relevance as rel
from buysmarter.llm.relevance import (
    KEEP_THRESHOLD,
    RELEVANCE_MODEL,
    RelevanceResult,
    accessory_reason,
    filter_relevant,
    filter_relevant_prompt,
    relevance_questions,
)

# --- Heuristic (pure) against real failure cases ---------------------------


def test_battery_dropped_for_vacuum() -> None:
    # Marker opens the title AND category noun absent → drop.
    assert accessory_reason("робот пылесос", "Аккумулятор для Dyson V8") is not None


def test_nozzles_dropped_genitive_plural() -> None:
    # "5 насадок …" — leading quantity skipped, marker opens the title → drop.
    assert accessory_reason("портативный ирригатор", "5 насадок для ирригатора") is not None


def test_product_with_nozzles_kept() -> None:
    # Category noun present and marker not at start → keep.
    assert accessory_reason("портативный ирригатор", "Xiaomi ирригатор F300, 4 насадки") is None


def test_real_product_mentioning_filter_kept() -> None:
    # "с HEPA фильтром" must not disqualify a genuine vacuum.
    assert accessory_reason("робот пылесос", "Робот-пылесос Xiaomi с HEPA фильтром") is None


def test_standalone_filter_dropped() -> None:
    assert accessory_reason("робот пылесос", "Фильтр HEPA для пылесоса") is not None


def test_plain_product_kept() -> None:
    assert accessory_reason("робот пылесос", "Робот-пылесос Roborock Q7 Max") is None


# --- Jev pass (stubbed system_one) ----------------------------------------


def _answers(*nouls: float) -> dict[str, dict]:
    return {f"t{i}": {"type": "noul", "noul": p} for i, p in enumerate(nouls)}


def test_questions_point_at_each_title() -> None:
    questions = relevance_questions(2)
    assert list(questions) == ["t0", "t1"]
    assert "`titles[1]`" in questions["t1"]["instructions"]
    assert questions["t0"]["type"] == "noul"
    assert "accessory" in questions["t0"]["criteria"]["false"]


async def test_filter_relevant_thresholds_nouls(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_system_one(state, questions):
        captured["state"] = state
        return _answers(0.92, 0.03, KEEP_THRESHOLD)

    monkeypatch.setattr(rel, "system_one", _fake_system_one)

    flags = await filter_relevant("робот пылесос", ["Робот", "Батарея", "Пылесос-робот"])

    assert captured["state"] == {
        "category": "робот пылесос",
        "titles": ["Робот", "Батарея", "Пылесос-робот"],
    }
    assert flags == [True, False, True]  # the threshold itself keeps


async def test_jev_error_falls_back_to_llm(monkeypatch) -> None:
    async def _boom(state, questions):
        raise rel.LlmError("gateway down")

    async def _llm(prompt, response_model, *, model=None, **kwargs):
        return RelevanceResult(relevant_indices=[1])

    monkeypatch.setattr(rel, "system_one", _boom)
    monkeypatch.setattr(rel, "generate", _llm)

    assert await filter_relevant("робот пылесос", ["Батарея", "Робот"]) == [False, True]


async def test_malformed_jev_answer_falls_back_to_llm(monkeypatch) -> None:
    async def _bad_shape(state, questions):
        return {"t0": {"type": "noul"}}  # no "noul" value

    async def _llm(prompt, response_model, *, model=None, **kwargs):
        return RelevanceResult(relevant_indices=[0])

    monkeypatch.setattr(rel, "system_one", _bad_shape)
    monkeypatch.setattr(rel, "generate", _llm)

    assert await filter_relevant("робот пылесос", ["a"]) == [True]


# --- LLM fallback (Jev is off in tests unless stubbed: conftest._no_live_jev) ---


def test_fallback_model_choice_is_free_stack() -> None:
    assert RELEVANCE_MODEL == "free-stack"


def test_prompt_numbers_titles() -> None:
    prompt = filter_relevant_prompt("робот пылесос", ["Робот A", "Батарея B"])
    assert "0. Робот A" in prompt
    assert "1. Батарея B" in prompt
    assert "робот пылесос" in prompt


async def test_fallback_drops_accessory_indices(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        captured["model"] = model
        return RelevanceResult(relevant_indices=[0, 2])

    monkeypatch.setattr(rel, "generate", _fake_generate)

    flags = await filter_relevant("робот пылесос", ["Робот", "Батарея", "Пылесос-робот"])

    assert captured["model"] == "free-stack"
    assert flags == [True, False, True]


async def test_fallback_fail_open_on_out_of_range(monkeypatch) -> None:
    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return RelevanceResult(relevant_indices=[0, 5])  # 5 is out of range

    monkeypatch.setattr(rel, "generate", _fake_generate)

    assert await filter_relevant("робот пылесос", ["a", "b", "c"]) == [True, True, True]


async def test_both_jev_and_llm_down_keeps_all(monkeypatch) -> None:
    async def _boom(prompt, response_model, *, model=None, **kwargs):
        raise rel.LlmError("cli down")

    monkeypatch.setattr(rel, "generate", _boom)

    assert await filter_relevant("робот пылесос", ["a", "b"]) == [True, True]


async def test_filter_relevant_empty_titles() -> None:
    assert await filter_relevant("робот пылесос", []) == []


def test_marker_inside_word_does_not_match() -> None:
    # "case" inside "showcase" must not trigger the accessory marker.
    assert accessory_reason("робот пылесос", "Showcase Xiaomi vacuum") is None


def test_cyrillic_marker_inside_compound_still_matches() -> None:
    assert accessory_reason("ирригатор", "Микрофильтр Waterpik") is not None


async def test_each_title_logs_its_score_against_the_threshold(monkeypatch, caplog) -> None:
    async def _fake_system_one(state, questions):
        return _answers(0.92, 0.031)

    monkeypatch.setattr(rel, "system_one", _fake_system_one)

    with caplog.at_level("INFO", logger=rel.__name__):
        await filter_relevant("робот пылесос", ["Робот", "Батарея"], keep_threshold=0.55)

    assert [r.getMessage() for r in caplog.records] == [
        "relevance score: 0.920 keep threshold=0.55 category='робот пылесос' title='Робот'",
        "relevance score: 0.031 drop threshold=0.55 category='робот пылесос' title='Батарея'",
    ]
