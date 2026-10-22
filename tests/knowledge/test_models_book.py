"""Tests for product-identity knowledge: ModelBook code/alias resolution."""

from buysmarter.knowledge.models_book import (
    IRRIGATOR_MODEL_BOOK,
    ModelBook,
    ModelEntry,
    get_model_book,
)


def test_bare_code_resolves_to_canonical():
    assert IRRIGATOR_MODEL_BOOK.resolve("MEO703") == "Xiaomi F300"


def test_code_and_alt_code_are_same_identity():
    # MEO703 == F300 (same product, two codes) — the identity the dedupe leans on.
    assert IRRIGATOR_MODEL_BOOK.resolve("MEO703") == IRRIGATOR_MODEL_BOOK.resolve("F300")
    assert IRRIGATOR_MODEL_BOOK.resolve("F300") == "Xiaomi F300"


def test_code_matches_inside_normal_title():
    assert IRRIGATOR_MODEL_BOOK.resolve("Xiaomi F300 irrigator") == "Xiaomi F300"


def test_hyphenated_code_matches():
    # "RL-670" / "RL 670" / "RL670" all normalize to the same code.
    assert IRRIGATOR_MODEL_BOOK.resolve("Revyline RL-670 портативный") == "Revyline RL 670"
    assert IRRIGATOR_MODEL_BOOK.resolve("ирригатор revyline rl670") == "Revyline RL 670"


def test_olx_typo_me_705_resolves_via_alias_only():
    # The real OLX case: "me 705" is an explicit alias for MEO705.
    assert IRRIGATOR_MODEL_BOOK.resolve("Ирригатор xiaomi me 705") == "Xiaomi Water Flosser 2"


def test_me705_does_not_fuzzy_match_meo705_code():
    # "me705" must NOT match code "meo705" — codes are exact, typos via aliases.
    book_no_alias = IRRIGATOR_MODEL_BOOK.model_copy(deep=True)
    for entry in book_no_alias.entries:
        entry.aliases = []
    assert book_no_alias.resolve("Ирригатор xiaomi me 705") is None


def test_code_not_extended_by_trailing_digit():
    # "RL220" must not match inside "RL2200".
    assert IRRIGATOR_MODEL_BOOK.resolve("Revyline RL2200 ирригатор") is None


def test_alias_only_waterpik_line_resolves():
    assert IRRIGATOR_MODEL_BOOK.resolve("Waterpik Cordless Advanced водный") == (
        "Waterpik Cordless Advanced"
    )


def test_unknown_title_stays_none():
    assert IRRIGATOR_MODEL_BOOK.resolve("Диван кожаный угловой") is None


def test_describe_appends_code():
    assert IRRIGATOR_MODEL_BOOK.describe("Ирригатор xiaomi me 705") == (
        "Xiaomi Water Flosser 2 (MEO705)"
    )


def test_describe_alias_only_has_no_code():
    assert IRRIGATOR_MODEL_BOOK.describe("Waterpik Pulse 3100") == "Waterpik Pulse 3100"


def test_get_model_book_matches_irrigator_keywords():
    assert get_model_book("ирригатор портативный") is IRRIGATOR_MODEL_BOOK
    assert get_model_book("water flosser") is IRRIGATOR_MODEL_BOOK
    assert get_model_book("irrigator") is IRRIGATOR_MODEL_BOOK


def test_get_model_book_unknown_category_returns_none():
    assert get_model_book("зубная щётка") is None


def test_empty_alias_or_code_does_not_match_every_title():
    from buysmarter.knowledge.models_book import ModelBook, ModelEntry

    book = ModelBook(entries=[ModelEntry(canonical="X", brand="B", aliases=["", " "], codes=[""])])
    assert book.resolve("anything at all") is None


def test_the_longest_matching_alias_wins_over_the_first_entry() -> None:
    book = ModelBook(
        entries=[
            ModelEntry(canonical="Samsung Galaxy S24", brand="Samsung", aliases=["S24"]),
            ModelEntry(canonical="Samsung Galaxy S24 FE", brand="Samsung", aliases=["S24 FE"]),
        ]
    )

    assert book.resolve("Samsung Galaxy S24 FE 128") == "Samsung Galaxy S24 FE"
    assert book.resolve("Samsung Galaxy S24 256") == "Samsung Galaxy S24"
