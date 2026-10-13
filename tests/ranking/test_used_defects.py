import pytest

from buysmarter.ranking.used_defects import find_defects, is_for_parts


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Экран треснут, остальное норм", ["трещины/сколы"]),
        ("Есть небольшой скол на корпусе", ["трещины/сколы"]),
        ("Не включается после падения", ["заявлена неисправность"]),
        ("Насадка сломана", ["заявлена неисправность"]),
        ("Был залит водой, просушили", ["был залит"]),
        ("Батарея не держит, хватает на час", ["слабый аккумулятор"]),
        ("Быстро разряжается", ["слабый аккумулятор"]),
        ("Аккумулятор (не держит заряд)", ["слабый аккумулятор"]),
        ("Меняли дисплей в сервисе", ["был в ремонте"]),
        ("Трещина на стекле. Был в ремонте", ["трещины/сколы", "был в ремонте"]),
    ],
)
def test_a_stated_defect_is_found(text, expected):
    assert find_defects(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Без трещин и сколов, всё работает",
        "Не вскрывался, ремонта не было",
        "Сколов нет. Не залит",
        "Сколько стоит доставка? Всё работает отлично",
        "Экран не разбит, батарея держит день",
        "Расходники поменяли",
        "",
    ],
)
def test_a_negated_or_absent_defect_is_not_flagged(text):
    assert find_defects(text) == []


def test_a_negation_in_another_clause_does_not_hide_a_defect():
    assert find_defects("Царапин нет, но экран треснут") == ["трещины/сколы"]


@pytest.mark.parametrize("text", ["Продаю на запчасти", "iPhone 11 на детали", "for parts only"])
def test_for_parts_listings_are_recognised(text):
    assert is_for_parts(text)


def test_a_working_item_is_not_for_parts():
    assert not is_for_parts("Запчасти не нужны, всё родное")
