"""Composition-driven wiring in the recommend service (stubbed extraction)."""

from buysmarter.capabilities import composition as svc
from buysmarter.capabilities.composition import (
    extract_ingredients_into,
    mark_title_substances,
    required_substances,
)
from buysmarter.knowledge.ingredients import ActiveSubstance, IngredientBook, composition_text
from buysmarter.llm.ingredients import DetectedIngredients
from buysmarter.ranking.models import Candidate, QueryConstraints


def _book() -> IngredientBook:
    return IngredientBook(
        substances=[
            ActiveSubstance(canonical="адапален", aliases=["adapalene", "Дифферин"]),
            ActiveSubstance(canonical="азелаиновая кислота", aliases=["azelaic acid"]),
        ]
    )


def _cand(pid: str, title: str, **specs) -> Candidate:
    return Candidate(source="kaspi", source_product_id=pid, title=title, price=5000, specs=specs)


def test_sostav_text_prefers_composition_key() -> None:
    cand = _cand("1", "Крем", **{"Состав": "адапален 0,1%", "Вес": "15г"})
    assert composition_text(cand.specs) == "адапален 0,1%"
    assert composition_text(_cand("2", "Крем").specs) == ""


def test_required_substances_from_category_and_features() -> None:
    book = _book()
    constraints = QueryConstraints(category="крем с адапаленом", required_features=["azelaic acid"])
    assert set(required_substances(book, constraints)) == {"адапален", "азелаиновая кислота"}


async def test_extract_ingredients_into_canonicalizes(monkeypatch) -> None:
    async def _fake_extract(category, book, items):
        return [
            DetectedIngredients(index=0, substances=["adapalene"], concentration="0,1%"),
            DetectedIngredients(index=1, substances=[], concentration=None),
        ]

    monkeypatch.setattr(svc, "extract_ingredients", _fake_extract)
    pool = [_cand("1", "Клензит", **{"Состав": "adapalene"}), _cand("2", "Крем")]

    await extract_ingredients_into(pool, "крем от акне", _book(), use_llm=True)

    assert pool[0].detected_substances == ["адапален"]  # canonicalized from alias
    assert pool[0].concentration == "0,1%"
    assert pool[1].detected_substances == []
    assert pool[0].ingredients_checked and pool[1].ingredients_checked  # both answered


async def test_extract_ingredients_into_skips_without_llm(monkeypatch) -> None:
    async def _boom(*args, **kwargs):
        raise AssertionError("should not call the LLM with use_llm=False")

    monkeypatch.setattr(svc, "extract_ingredients", _boom)
    pool = [_cand("1", "Клензит")]
    await extract_ingredients_into(pool, "крем от акне", _book(), use_llm=False)
    assert pool[0].detected_substances == []


async def test_extract_ingredients_into_failure_leaves_candidates_unchecked(monkeypatch) -> None:
    async def _no_answer(category, book, items):
        return [None] * len(items)

    monkeypatch.setattr(svc, "extract_ingredients", _no_answer)
    pool = [_cand("1", "Крем", **{"Состав": "вода"}), _cand("2", "Гель")]

    await extract_ingredients_into(pool, "крем от акне", _book(), use_llm=True)

    assert [c.ingredients_checked for c in pool] == [False, False]
    assert [c.detected_substances for c in pool] == [[], []]


async def test_extract_ingredients_into_marks_only_answered_items(monkeypatch) -> None:
    async def _partial(category, book, items):
        return [None, DetectedIngredients(index=1)]

    monkeypatch.setattr(svc, "extract_ingredients", _partial)
    pool = [_cand("1", "Крем"), _cand("2", "Гель")]

    await extract_ingredients_into(pool, "крем от акне", _book(), use_llm=True)

    assert [c.ingredients_checked for c in pool] == [False, True]


def test_title_naming_a_required_substance_counts_as_checked() -> None:
    pool = [_cand("1", "Дифферин крем 0,1%"), _cand("2", "Крем увлажняющий", Состав="вода")]

    mark_title_substances(pool, _book(), ["адапален"])

    assert pool[0].detected_substances == ["адапален"]  # via the alias in the title
    assert pool[0].ingredients_checked
    assert pool[1].detected_substances == []
    assert not pool[1].ingredients_checked


async def test_extraction_keeps_the_title_substance_and_adds_concentration(monkeypatch) -> None:
    async def _missed_it(category, book, items):
        return [DetectedIngredients(index=0, substances=[], concentration="0,1%")]

    monkeypatch.setattr(svc, "extract_ingredients", _missed_it)
    pool = [_cand("1", "Клензит гель с адапаленом")]
    mark_title_substances(pool, _book(), ["адапален"])

    await extract_ingredients_into(pool, "крем от акне", _book(), use_llm=True)

    assert pool[0].detected_substances == ["адапален"]
    assert pool[0].concentration == "0,1%"
    assert pool[0].ingredients_checked
