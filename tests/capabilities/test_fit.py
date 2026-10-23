"""Fit: the device the goods are for ("чехол на iPhone 15"), a request capability."""

import pytest

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.fit import fit_target
from buysmarter.capabilities.flow import activate, read_query
from buysmarter.knowledge.profile import ResolvedProfile
from buysmarter.llm.prompts import parse_query_naive
from buysmarter.ranking.engine import rank
from buysmarter.ranking.model_tokens import names_model
from buysmarter.ranking.models import Candidate, QueryConstraints


@pytest.mark.parametrize(
    ("query", "target"),
    [
        ("зарядка для xiaomi 13", "Xiaomi 13"),
        ("чехол на айфон 15", "iPhone 15"),
        ("стекло на S24", "S24"),
        ("чехол для iPhone 15 Pro Max до 20000", "iPhone 15 Pro Max"),
        ("крем для лица", None),
        ("кроссовки на зиму", None),
        ("куртка для детей", None),
        ("флешка на 64 GB", None),
        ("повербанк на 20000 mAh", None),
        ("смартфон iPhone 15", None),
        ("лампа на E27", None),
        ("зарядка на 65W", None),
        ("повербанк на 20000mAh", None),
        ("флешка на 64GB", None),
        ("лампа на 10W", None),
        ("кабель на 2m", None),
        ("кабель на 1.5м", None),
        ("зарядка на 65Вт", None),
        ("пылесос на 6000Pa", None),
        ("чехол к айфону 15", "iPhone 15"),
    ],
)
def test_fit_target_from_the_query_text(query, target):
    assert fit_target(query) == target


def _read(query: str, constraints: QueryConstraints | None = None) -> QueryConstraints:
    constraints = constraints or parse_query_naive(query)
    return read_query(REGISTRY, constraints, query=query, use_llm=False)


def test_query_names_the_target_in_the_search_and_not_in_the_product_type():
    read = _read("чехол на iPhone 15")

    assert read.fit_target == "iPhone 15"
    assert read.search_phrase == "чехол iPhone 15"
    assert read.product_type == "чехол"


def test_a_parse_without_the_model_in_the_search_phrase_gets_it_back():
    parsed = QueryConstraints(
        search_phrase="зарядка", product_type="зарядка", fit_target="Xiaomi 13"
    )

    read = _read("зарядка для Xiaomi 13", parsed)

    assert read.search_phrase == "зарядка Xiaomi 13"


@pytest.mark.parametrize("parsed_target", ["65W", "64GB", "2m", "20000mAh"])
def test_a_glued_unit_the_parser_called_a_device_is_dropped(parsed_target):
    parsed = QueryConstraints(
        search_phrase="зарядка", product_type="зарядка", fit_target=parsed_target
    )

    read = _read("зарядка на " + parsed_target, parsed)

    assert read.fit_target is None
    assert read.search_phrase == "зарядка"


def test_no_fit_leaves_the_constraints_alone():
    parsed = parse_query_naive("крем для лица")

    assert _read("крем для лица", parsed) == parsed


@pytest.mark.parametrize(
    ("title", "fits"),
    [
        ("Чехол для Apple iPhone 15 прозрачный", True),
        ("Чехол iPhone15 силикон", True),
        ("Стекло для iPhone 15/15 Pro", True),
        ("Чехол для iPhone 15 Pro", False),
        ("Чехол для iPhone 15 Plus", False),
        ("Чехол для iPhone 13", False),
        ("Чехол для iPhone 150", False),
    ],
)
def test_names_model_needs_the_whole_model(title, fits):
    assert names_model(title, "iPhone 15") is fits


def _cand(pid: str, title: str) -> Candidate:
    return Candidate(source="kaspi", source_product_id=pid, title=title, price=5_000, rating=4.8)


def _rank(query: str, titles: list[str]):
    constraints = _read(query)
    active = activate(REGISTRY, ResolvedProfile(slug="x"), constraints, query=query, use_llm=False)
    pool = [_cand(str(i), t) for i, t in enumerate(titles)]
    return rank(pool, constraints, query=query, hooks=active.rank_hooks())


def test_a_case_for_another_model_is_penalized_once_and_the_fit_gets_a_pro():
    rec = _rank("стекло на S24", ["Стекло Galaxy S24", "Стекло Galaxy S24 Ultra"])

    by_title = {s.candidate.title: s for s in rec.ranked}
    fits, other = by_title["Стекло Galaxy S24"], by_title["Стекло Galaxy S24 Ultra"]
    assert fits.total_score > other.total_score
    assert "подходит к S24" in fits.pros
    assert [c for c in other.cons if c.startswith("другая модель")] == [
        "другая модель: в названии нет «S24»"
    ]


def test_no_fitting_listing_in_the_pool_means_no_penalty():
    rec = _rank("масло для Kia Rio 2018", ["Масло моторное 5W-30", "Масло Mobil 5W-40"])

    assert all(not c.startswith("другая модель") for s in rec.ranked for c in s.cons)
