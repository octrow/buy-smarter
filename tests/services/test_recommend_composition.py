"""Composition-driven wiring in the recommend service (stubbed extraction)."""

from buysmarter.knowledge.ingredients import ActiveSubstance, IngredientBook
from buysmarter.llm.ingredients import DetectedIngredients
from buysmarter.ranking.models import Candidate, QueryConstraints
from buysmarter.services import recommend as svc
from buysmarter.services.recommend import (
    _extract_ingredients_into,
    _required_substances,
    _sostav_text,
)


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
    assert _sostav_text(cand) == "адапален 0,1%"
    assert _sostav_text(_cand("2", "Крем")) == ""


def test_required_substances_from_category_and_features() -> None:
    book = _book()
    constraints = QueryConstraints(category="крем с адапаленом", required_features=["azelaic acid"])
    assert set(_required_substances(book, constraints)) == {"адапален", "азелаиновая кислота"}


async def test_extract_ingredients_into_canonicalizes(monkeypatch) -> None:
    async def _fake_extract(category, book, items):
        return [
            DetectedIngredients(index=0, substances=["adapalene"], concentration="0,1%"),
            DetectedIngredients(index=1, substances=[], concentration=None),
        ]

    monkeypatch.setattr(svc, "extract_ingredients", _fake_extract)
    pool = [_cand("1", "Клензит", **{"Состав": "adapalene"}), _cand("2", "Крем")]

    await _extract_ingredients_into(pool, "крем от акне", _book(), use_llm=True)

    assert pool[0].detected_substances == ["адапален"]  # canonicalized from alias
    assert pool[0].concentration == "0,1%"
    assert pool[1].detected_substances == []


async def test_extract_ingredients_into_skips_without_llm(monkeypatch) -> None:
    async def _boom(*args, **kwargs):
        raise AssertionError("should not call the LLM with use_llm=False")

    monkeypatch.setattr(svc, "extract_ingredients", _boom)
    pool = [_cand("1", "Клензит")]
    await _extract_ingredients_into(pool, "крем от акне", _book(), use_llm=False)
    assert pool[0].detected_substances == []
