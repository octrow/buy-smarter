"""Ingredient extraction: stubbed LLM, index mapping, and fail-open behavior."""

from buysmarter.knowledge.ingredients import ActiveSubstance, IngredientBook
from buysmarter.llm import ingredients as ingredients_mod
from buysmarter.llm.ingredients import (
    DetectedIngredients,
    IngredientExtractionResult,
    extract_ingredients,
)


def _book() -> IngredientBook:
    return IngredientBook(substances=[ActiveSubstance(canonical="адапален", aliases=["adapalene"])])


async def test_extract_ingredients_maps_by_index(monkeypatch) -> None:
    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return IngredientExtractionResult(
            detections=[
                DetectedIngredients(index=1, substances=["адапален"], concentration="0,1%"),
                DetectedIngredients(index=0, substances=[], concentration=None),
            ]
        )

    monkeypatch.setattr(ingredients_mod, "generate", _fake_generate)
    items = [("Крем увлажняющий", ""), ("Клензит гель", "адапален 0,1%")]

    result = await extract_ingredients("крем от акне", _book(), items)
    assert result[0].substances == []
    assert result[1].substances == ["адапален"]
    assert result[1].concentration == "0,1%"


async def test_extract_ingredients_empty_items() -> None:
    assert await extract_ingredients("крем от акне", _book(), []) == []


async def test_extract_ingredients_fail_open(monkeypatch) -> None:
    async def _boom(prompt, response_model, *, model=None, **kwargs):
        raise ingredients_mod.LlmError("cli down")

    monkeypatch.setattr(ingredients_mod, "generate", _boom)
    items = [("a", ""), ("b", "")]

    result = await extract_ingredients("крем от акне", _book(), items)
    assert len(result) == 2
    assert all(d.substances == [] and d.concentration is None for d in result)


async def test_extract_ingredients_ignores_out_of_range_index(monkeypatch) -> None:
    async def _fake_generate(prompt, response_model, *, model=None, **kwargs):
        return IngredientExtractionResult(
            detections=[DetectedIngredients(index=99, substances=["адапален"])]
        )

    monkeypatch.setattr(ingredients_mod, "generate", _fake_generate)
    result = await extract_ingredients("крем от акне", _book(), [("a", "")])
    assert result[0].substances == []


# --- Jev first (book substances + selected concentration), LLM fallback ----


def _two_substance_book() -> IngredientBook:
    return IngredientBook(
        substances=[
            ActiveSubstance(canonical="адапален", aliases=["adapalene"]),
            ActiveSubstance(canonical="бензоила пероксид"),
        ]
    )


def _llm_must_not_run(*args, **kwargs):
    raise AssertionError("LLM fallback ran although Jev answered")


def test_jev_questions_cover_items_x_substances_and_found_percentages() -> None:
    items = [("Клензит гель 0,1%", "адапален 1 мг/г"), ("Крем увлажняющий", "")]

    state, questions, percents = ingredients_mod.build_ingredient_questions(
        "крем от акне", _two_substance_book(), items
    )

    assert set(questions) == {"i0s0", "i0s1", "c0", "i1s0", "i1s1"}  # no % in item 1
    assert percents == [["0,1%"], []]
    assert list(questions["c0"]["criteria"]) == ["0,1%", ingredients_mod._NO_CONCENTRATION]
    assert state["items"][1]["composition"] == "(none)"


async def test_jev_detects_book_substances_and_copies_concentration(monkeypatch) -> None:
    async def _jev(state, questions):
        return {
            "i0s0": {"type": "noul", "noul": 0.99},
            "i0s1": {"type": "noul", "noul": 0.02},
            "c0": {"type": "choice", "choice": "0,1%", "probabilities": {"0,1%": 0.98}},
            "i1s0": {"type": "noul", "noul": 0.03},
            "i1s1": {"type": "noul", "noul": 0.04},
        }

    monkeypatch.setattr(ingredients_mod, "system_one", _jev)
    monkeypatch.setattr(ingredients_mod, "generate", _llm_must_not_run)
    items = [("Клензит гель 0,1%", "адапален 1 мг/г"), ("Крем увлажняющий", "")]

    result = await extract_ingredients("крем от акне", _two_substance_book(), items)

    assert (result[0].substances, result[0].concentration) == (["адапален"], "0,1%")
    assert (result[1].substances, result[1].concentration) == ([], None)


async def test_jev_no_concentration_pick_leaves_it_unknown(monkeypatch) -> None:
    async def _jev(state, questions):
        none = ingredients_mod._NO_CONCENTRATION
        return {
            "i0s0": {"type": "noul", "noul": 0.97},
            "c0": {"type": "choice", "choice": none, "probabilities": {none: 0.9}},
        }

    monkeypatch.setattr(ingredients_mod, "system_one", _jev)
    items = [("Гель с адапаленом, скидка 30%", "")]

    result = await extract_ingredients("крем от акне", _book(), items)

    assert (result[0].substances, result[0].concentration) == (["адапален"], None)


async def test_empty_book_goes_straight_to_llm(monkeypatch) -> None:
    async def _jev_must_not_run(state, questions):
        raise AssertionError("Jev has nothing to check with an empty book")

    async def _llm(prompt, response_model, *, model=None, **kwargs):
        return IngredientExtractionResult(
            detections=[DetectedIngredients(index=0, substances=["ниацинамид"])]
        )

    monkeypatch.setattr(ingredients_mod, "system_one", _jev_must_not_run)
    monkeypatch.setattr(ingredients_mod, "generate", _llm)

    result = await extract_ingredients("сыворотка", IngredientBook(), [("Сыворотка", "")])

    assert result[0].substances == ["ниацинамид"]


async def test_jev_error_falls_back_to_llm(monkeypatch) -> None:
    async def _boom(state, questions):
        raise ingredients_mod.LlmError("gateway down")

    async def _llm(prompt, response_model, *, model=None, **kwargs):
        return IngredientExtractionResult(
            detections=[DetectedIngredients(index=0, substances=["адапален"])]
        )

    monkeypatch.setattr(ingredients_mod, "system_one", _boom)
    monkeypatch.setattr(ingredients_mod, "generate", _llm)

    result = await extract_ingredients("крем от акне", _book(), [("Клензит", "")])

    assert result[0].substances == ["адапален"]
