"""Audience (men/women/kids) filter and size parsing."""

from buysmarter.ranking.audience import (
    FEMALE,
    KIDS,
    MALE,
    is_size_phrase,
    other_audience,
    query_audience,
    query_size,
    strip_size,
)
from buysmarter.ranking.filters import apply_hard_filters
from buysmarter.ranking.models import Candidate, QueryConstraints
from tests.fakes import capability_hooks


def test_query_audience_reads_the_single_audience_named():
    assert query_audience("мужские кроссовки до 35000") == MALE
    assert query_audience("женский парфюм") == FEMALE
    assert query_audience("детская куртка") == KIDS
    assert query_audience("кроссовки") is None
    assert query_audience("кроссовки для мужчин и женщин") is None


def test_other_audience_flags_only_a_listing_marked_for_someone_else():
    assert other_audience(MALE, "Кроссовки K2 collection женские кроссовки") == FEMALE
    assert other_audience(MALE, "Кроссовки для мальчиков") == KIDS
    assert other_audience(MALE, "Кроссовки Puma Sneakers Unisex женские") is None
    assert other_audience(MALE, "Кроссовки для мужчин и женщин") is None
    assert other_audience(MALE, "Кроссовки Demix") is None


def test_hard_filter_drops_other_audience_using_the_category_path_too():
    women = Candidate(
        source="kaspi",
        source_product_id="1",
        title="Кроссовки FASHION Yeezy Boost 350",
        price=9_000,
        category_path=["Обувь", "Женская обувь", "Женские кроссовки и кеды"],
    )
    men = women.model_copy(
        update={"source_product_id": "2", "category_path": ["Обувь", "Мужская обувь"]}
    )

    constraints = QueryConstraints(search_phrase="мужские кроссовки", gender=MALE)
    passed, rejected = apply_hard_filters([women, men], constraints, capability_hooks(constraints))

    assert passed == [men]
    assert rejected[0][1] == "other audience: female"


def test_size_is_parsed_and_stripped_from_the_search_phrase():
    assert query_size("мужские кроссовки 42 размер") == "42"
    assert query_size("кроссовки размер 42,5") == "42.5"
    assert query_size("куртка размер xl") == "XL"
    assert query_size("кроссовки до 35000") is None
    assert strip_size("мужские кроссовки 42 размер") == "мужские кроссовки"
    assert is_size_phrase("42 размер")
    assert not is_size_phrase("IPX7")
