import pytest

from buysmarter.normalize.city import find_city, strip_city


@pytest.mark.parametrize(
    ("text", "city"),
    [
        ("ирригатор Алматы", "Алматы"),
        ("ирригатор almaty", "Алматы"),
        ("пылесос в Шымкенте", "Шымкент"),
        ("доставка из Караганды", "Караганда"),
        ("по Астане", "Астана"),
        ("Нур-Султан", "Астана"),
        ("в Костанае", "Костанай"),
        ("в Уральске", "Уральск"),
        ("г. Усть-Каменогорск", "Усть-Каменогорск"),
        ("Kokshetau", "Кокшетау"),
    ],
)
def test_a_known_city_is_found_in_any_case_form(text: str, city: str) -> None:
    assert find_city(text) == city


@pytest.mark.parametrize(
    "text", ["уральские пельмени", "семейный набор", "семена томатов", "Москва", "", None]
)
def test_words_that_only_look_like_a_city_are_not_one(text: str | None) -> None:
    assert find_city(text) is None


def test_strip_city_leaves_the_rest_of_the_query() -> None:
    assert strip_city("пылесос в Шымкенте до 50000").split() == ["пылесос", "в", "до", "50000"]
