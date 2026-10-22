"""Substance-targeted recall + required-substance hard filter in the service.

The live failure this covers: query "крем с адапаленом" returned only
moisturizers (no adapalene), yet a moisturizer won as best pick. Fix: extra
Kaspi searches per required substance and its equivalence-group product names
surface the real adapalene products (Клензит...), and any candidate proven to
lack the substance is hard-filtered out — never the best pick. When the filter
empties the pool we refuse to fall back and raise ``NoSubstanceMatch``.
"""

import pytest
from market_sources.base import RawProduct

from buysmarter.knowledge.ingredients import (
    ActiveSubstance,
    EquivalentProduct,
    IngredientBook,
)
from buysmarter.knowledge.store import COMPOSITION_DRIVEN, SPEC_DRIVEN
from buysmarter.llm.ingredients import DetectedIngredients
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services import recommend as svc
from buysmarter.services.recommend import NoSubstanceMatch, recommend


def _book() -> IngredientBook:
    return IngredientBook(
        substances=[
            ActiveSubstance(
                canonical="адапален",
                aliases=["adapalene"],
                equivalents=[
                    EquivalentProduct(name="Клензит"),
                    EquivalentProduct(name="Дифферин"),
                    EquivalentProduct(name="Адаклин"),
                ],
            )
        ]
    )


def _rp(pid: str, title: str, price: float = 6000) -> RawProduct:
    return RawProduct(source="kaspi", product_id=pid, title=title, price=price)


class _StubKaspi:
    """Serves per-query cards + per-product specs, records issued queries."""

    name = "kaspi"

    def __init__(self, main, by_query, specs_by_id) -> None:
        self._main = main
        self._by_query = by_query
        self._specs = specs_by_id
        self.queries: list[str] = []

    async def search(self, query: str, city: str | None = None) -> list[RawProduct]:
        self.queries.append(query)
        return self._by_query.get(query, self._main)

    async def specs(self, product_id: str) -> dict[str, str]:
        return self._specs.get(product_id, {})

    async def offers(self, product_id: str):
        return []

    async def reviews(self, product_id: str):
        return []


def _patch_composition(monkeypatch, book: IngredientBook) -> None:
    async def _fake_resolve_composition(category: str, *, use_llm: bool = True):
        return COMPOSITION_DRIVEN, book

    monkeypatch.setattr(svc, "resolve_composition", _fake_resolve_composition)


def _patch_extraction(monkeypatch) -> None:
    async def _fake_extract(category, book, items):
        out = []
        for i, (title, sostav) in enumerate(items):
            blob = f"{title} {sostav}".lower()
            if "адапален" in blob or "клензит" in blob:
                out.append(
                    DetectedIngredients(index=i, substances=["адапален"], concentration="0.1%")
                )
            else:
                out.append(DetectedIngredients(index=i))
        return out

    monkeypatch.setattr(svc, "extract_ingredients", _fake_extract)
    monkeypatch.setattr(svc, "_no_llm_route", lambda: False)  # the fakes stand in for an LLM


_CONSTRAINTS = QueryConstraints(category="крем с адапаленом", budget_max=20000)


async def test_substance_searches_issued_with_equivalence_names(monkeypatch) -> None:
    _patch_composition(monkeypatch, _book())
    _patch_extraction(monkeypatch)
    stub = _StubKaspi(
        main=[_rp("bio", "Bioderma Sebium Hydra крем")],
        by_query={"Клензит": [_rp("kl", "Клензит гель адапален 0.1%")]},
        specs_by_id={
            "bio": {"Состав": "вода, глицерин"},
            "kl": {"Состав": "адапален 0.1%"},
        },
    )

    await recommend(
        "крем с адапаленом",
        use_llm=True,
        source=stub,
        sources=["kaspi"],
        parsed=_CONSTRAINTS,
        persist=False,
        with_review_summary=False,
    )

    # The substance itself AND each equivalence-group product name got searched.
    assert {"адапален", "Клензит", "Дифферин", "Адаклин"} <= set(stub.queries)


async def test_adapalene_product_wins_moisturizer_filtered(monkeypatch) -> None:
    _patch_composition(monkeypatch, _book())
    _patch_extraction(monkeypatch)
    stub = _StubKaspi(
        main=[_rp("bio", "Bioderma Sebium Hydra крем", 12000)],
        by_query={"Клензит": [_rp("kl", "Клензит гель адапален 0.1%", 6000)]},
        specs_by_id={
            "bio": {"Состав": "вода, глицерин"},
            "kl": {"Состав": "адапален 0.1%"},
        },
    )

    result = await recommend(
        "крем с адапаленом",
        use_llm=True,
        source=stub,
        sources=["kaspi"],
        parsed=_CONSTRAINTS,
        persist=False,
        with_review_summary=False,
    )

    rec = result.recommendation
    assert rec.best.candidate.source_product_id == "kl"
    ingredient = next(a for a in rec.best.axes if a.name == "ingredient")
    assert ingredient.score == ingredient.max_score  # full 25/25 credit
    # The moisturizer proven to lack adapalene is filtered, never surfaced.
    assert "bio" not in {s.candidate.source_product_id for s in rec.ranked}
    assert rec.rejected_count >= 1
    assert not result.ingredients_unchecked  # every ranked candidate got an answer


async def test_empty_pool_raises_no_substance_match(monkeypatch) -> None:
    _patch_composition(monkeypatch, _book())
    _patch_extraction(monkeypatch)
    stub = _StubKaspi(
        main=[_rp("bio", "Bioderma Sebium Hydra крем", 12000)],
        by_query={},  # substance searches surface nothing
        specs_by_id={"bio": {"Состав": "вода, глицерин"}},
    )

    with pytest.raises(NoSubstanceMatch) as exc_info:
        await recommend(
            "крем с адапаленом",
            use_llm=True,
            source=stub,
            sources=["kaspi"],
            parsed=_CONSTRAINTS,
            persist=False,
            with_review_summary=False,
        )

    exc = exc_info.value
    assert exc.substances == ["адапален"]
    assert exc.checked == 1
    assert [c.source_product_id for c in exc.alternatives] == ["bio"]
    assert "адапален" in exc.message()


async def test_spec_driven_category_has_no_substance_recall(monkeypatch) -> None:
    async def _spec_driven(category: str, *, use_llm: bool = True):
        return SPEC_DRIVEN, None

    monkeypatch.setattr(svc, "resolve_composition", _spec_driven)
    stub = _StubKaspi(main=[_rp("x", "Kingston SSD 480GB")], by_query={}, specs_by_id={})

    result = await recommend(
        "ssd",
        use_llm=False,
        source=stub,
        sources=["kaspi"],
        parsed=QueryConstraints(category="ssd"),
        persist=False,
        with_review_summary=False,
    )

    # No brand book, spec-driven → only the single main-category search.
    assert stub.queries == ["ssd"]
    assert not result.ingredients_unchecked  # no composition to check


async def test_failed_extraction_never_raises_no_substance_match(monkeypatch) -> None:
    _patch_composition(monkeypatch, _book())

    async def _no_answer(category, book, items):
        return [None] * len(items)

    monkeypatch.setattr(svc, "extract_ingredients", _no_answer)
    monkeypatch.setattr(svc, "_no_llm_route", lambda: False)
    stub = _StubKaspi(
        main=[_rp("bio", "Bioderma Sebium Hydra крем", 12000)],
        by_query={},
        specs_by_id={"bio": {"Состав": "вода, глицерин"}},
    )

    result = await recommend(
        "крем с адапаленом",
        use_llm=True,
        source=stub,
        sources=["kaspi"],
        parsed=_CONSTRAINTS,
        persist=False,
        with_review_summary=False,
    )

    # Unchecked is not absent: the candidate stays, nothing rejected for the substance.
    assert result.recommendation.best.candidate.source_product_id == "bio"
    assert result.recommendation.rejected_count == 0


async def test_title_substance_survives_a_failed_extraction(monkeypatch) -> None:
    _patch_composition(monkeypatch, _book())

    async def _no_answer(category, book, items):
        return [None] * len(items)

    monkeypatch.setattr(svc, "extract_ingredients", _no_answer)
    monkeypatch.setattr(svc, "_no_llm_route", lambda: False)
    stub = _StubKaspi(
        main=[_rp("kl", "Клензит гель", 6000)],
        by_query={},
        specs_by_id={"kl": {"Состав": "carbomer, propylene glycol"}},
    )

    result = await recommend(
        "крем с адапаленом",
        use_llm=True,
        source=stub,
        sources=["kaspi"],
        parsed=_CONSTRAINTS,
        persist=False,
        with_review_summary=False,
    )

    best = result.recommendation.best.candidate
    assert best.source_product_id == "kl"
    assert best.detected_substances == ["адапален"]


async def test_no_llm_composition_run_flags_unchecked_ingredients(monkeypatch) -> None:
    _patch_composition(monkeypatch, _book())

    async def _must_not_run(category, book, items):
        raise AssertionError("no extraction with use_llm=False")

    monkeypatch.setattr(svc, "extract_ingredients", _must_not_run)
    stub = _StubKaspi(
        main=[_rp("bio", "Bioderma Sebium Hydra крем", 12000)],
        by_query={"Клензит": [_rp("kl", "Клензит гель", 6000)]},
        specs_by_id={"bio": {"Состав": "вода, глицерин"}},
    )

    result = await recommend(
        "крем с адапаленом",
        use_llm=False,
        source=stub,
        sources=["kaspi"],
        parsed=_CONSTRAINTS,
        persist=False,
        with_review_summary=False,
    )

    assert result.ingredients_unchecked
    by_id = {s.candidate.source_product_id: s for s in result.recommendation.ranked}
    assert by_id["kl"].candidate.ingredients_checked  # the title names an equivalent
    assert not by_id["bio"].candidate.ingredients_checked
