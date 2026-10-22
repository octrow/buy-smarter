"""Budget out of a free-text query: money words read as money, units never do."""

import pytest

from buysmarter.llm.budget import FOREIGN_CURRENCY_NOTE, parse_budget
from buysmarter.llm.prompts import parse_query_naive


@pytest.mark.parametrize(
    ("query", "budget"),
    [
        ("кроссовки до 50 штук", 50_000),
        ("за 50 штук", 50_000),
        ("50 мың теңге", 50_000),
        ("косарь", 1_000),
        ("наушники за 2 косаря", 2_000),
        ("до 100к", 100_000),
        ("наушники 50к", 50_000),
        ("до 1500", 1_500),
        ("до 50", 50_000),
        ("до 15 000", 15_000),
        ("до 3 тыс", 3_000),
        ("до 40000 тг", 40_000),
        ("15 000₸", 15_000),
        ("салфетки до 15000 3 упаковки", 15_000),
        ("телевизор 4K до 300 тыс", 300_000),
    ],
)
def test_money(query: str, budget: int) -> None:
    assert parse_budget(query).budget_max == budget


@pytest.mark.parametrize(
    ("query", "low", "high"),
    [("30-40 тыс", 30_000, 40_000), ("от 30 до 40 тыс", 30_000, 40_000)],
)
def test_range(query: str, low: int, high: int) -> None:
    parsed = parse_budget(query)
    assert (parsed.budget_min, parsed.budget_max) == (low, high)


@pytest.mark.parametrize(
    "query", ["$500", "до 500$", "до 300 usd", "до 2000 руб", "до 2000 рублей", "до 900₽"]
)
def test_other_currency_is_noted_not_read(query: str) -> None:
    parsed = parse_budget(query)
    assert parsed.budget_max is None
    assert parsed.budget_note == FOREIGN_CURRENCY_NOTE


@pytest.mark.parametrize(
    "query",
    [
        "телевизор 4K",
        "телевизор 4к",
        "салфетки 120 шт",
        "диван до 2 м",
        "ёлка до 1.8 м",
        "собака до 18 кг",
        "игрушки до 3 лет",
        "пробники до 50 мл",
        "зарядка до 65 Вт",
        "витамин D3 до 5000 МЕ",
        "iPhone 15",
        "шины 205/55 R16",
        "кроссовки 42 размер",
        "кроссовки 50 штук",
    ],
)
def test_not_money(query: str) -> None:
    parsed = parse_budget(query)
    assert parsed.budget_max is None
    assert parsed.budget_note is None


@pytest.mark.parametrize(
    ("query", "phrase"),
    [
        ("кроссовки до 50 штук", "кроссовки"),
        ("наушники за 2 косаря", "наушники"),
        ("телевизор 4K до 300 тыс", "телевизор 4K"),
        ("ноутбук 30-40 тыс тг", "ноутбук"),
        ("айфон до 500$", "айфон"),
        ("ёлка до 1.8 м", "ёлка до 1.8 м"),
        ("2 рубашки до 20000", "2 рубашки"),
    ],
)
def test_money_words_leave_the_phrase(query: str, phrase: str) -> None:
    assert parse_budget(query).text == phrase


def test_a_word_starting_like_rubles_is_not_a_currency() -> None:
    parsed = parse_budget("2 рубашки до 20000")
    assert (parsed.budget_max, parsed.budget_note) == (20_000, None)


def test_naive_parse_reads_the_budget_and_keeps_units() -> None:
    assert parse_query_naive("кроссовки до 50 штук").search_phrase == "кроссовки"
    assert parse_query_naive("кроссовки до 50 штук").budget_max == 50_000
    tree = parse_query_naive("ёлка до 1.8 м")
    assert tree.budget_max is None
    assert tree.search_phrase == "ёлка 1.8 м"


@pytest.mark.parametrize(
    ("query", "budget", "phrase"),
    [
        ("автокресло до 18.5 кг", None, "автокресло до 18.5 кг"),
        ("ноутбук до 15.6 дюймов", None, "ноутбук до 15.6 дюймов"),
        ("наушники до 15.000", 15_000, "наушники"),
        ("фен до 150.000 тг", 150_000, "фен"),
        ("фен до 25,5 тыс", None, "фен до 25,5 тыс"),
    ],
)
def test_a_fraction_is_never_cut_into_a_budget(query: str, budget, phrase: str) -> None:
    """Review: backtracking gave "18.5 кг" a 1 000 ₸ budget ("1" of "18") and cut the phrase."""
    parsed = parse_budget(query)

    assert parsed.budget_max == budget
    assert parsed.text == phrase


@pytest.mark.parametrize(
    ("query", "budget", "phrase"),
    [
        ("iPhone 15 до 500 тыс", 500_000, "iPhone 15"),
        ("телевизор 43 до 150к", 150_000, "телевизор 43"),
        ("мужские кроссовки 42 до 30 тыс", 30_000, "мужские кроссовки 42"),
        ("кроссовки nike 2024 до 50к", 50_000, "кроссовки nike 2024"),
        ("айфон 15 128 до 400 тыс", 400_000, "айфон 15 128"),
    ],
)
def test_a_model_or_size_number_before_до_is_not_a_range(query: str, budget: int, phrase: str):
    """Review: "N до M" without «от» took the model number as budget_min and cut it."""
    parsed = parse_budget(query)

    assert (parsed.budget_min, parsed.budget_max) == (None, budget)
    assert parsed.text == phrase


def test_a_range_whose_ends_are_reversed_is_dropped() -> None:
    assert parse_budget("от 50 до 30 тыс").budget_min is None


@pytest.mark.parametrize(
    ("query", "budget", "phrase"),
    [
        ("құлаққап 15000 теңгеге дейін", 15_000, "құлаққап"),
        ("20 мыңға дейін", 20_000, ""),
        ("смартфон до 1 млн тенге", 1_000_000, "смартфон"),
        ("ноутбук 1-2 млн", 2_000_000, "ноутбук"),
    ],
)
def test_kazakh_forms_and_millions(query: str, budget: int, phrase: str) -> None:
    """Decision 10 promised «мың» and «теңге»; «дейін» means «до»; «млн» is ×1 000 000."""
    parsed = parse_budget(query)

    assert parsed.budget_max == budget
    assert parsed.text == phrase
