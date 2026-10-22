"""Request understanding: restricted goods and the clarification decision (phase 2)."""

import pytest

from buysmarter.services.understanding import restricted_kind, restricted_note


@pytest.mark.parametrize(
    ("query", "kind"),
    [
        ("вейп", "tobacco"),
        ("жижа для вейпа", "tobacco"),
        ("электронная сигарета", "tobacco"),
        ("сигареты Marlboro", "tobacco"),
        ("табак для кальяна", "tobacco"),
        ("кальян", "tobacco"),
        ("вино красное сухое", "alcohol"),
        ("пиво", "alcohol"),
        ("водка", "alcohol"),
        ("коньяк армянский", "alcohol"),
        ("виски", "alcohol"),
        ("алкоголь на праздник", "alcohol"),
        ("травмат", "weapon"),
        ("травматический пистолет", "weapon"),
        ("нож-бабочка", "weapon"),
    ],
)
def test_the_static_list_flags_restricted_goods(query: str, kind: str) -> None:
    assert restricted_kind(query) == kind


@pytest.mark.parametrize(
    "query",
    [
        "винный бокал",
        "бокал для вина",
        "пивная кружка",
        "кружка для пива",
        "травматологический ортез",
        "кальянный уголь",
        "уголь для кальяна",
        "пневматическая винтовка",
        "безалкогольное пиво",
        "винтовка",
        "кроссовки",
    ],
)
def test_stop_contexts_and_other_goods_are_not_restricted(query: str) -> None:
    assert restricted_kind(query) is None


def test_each_kind_has_a_warning_and_none_has_none() -> None:
    for kind in ("rx", "alcohol", "tobacco", "weapon"):
        assert restricted_note(kind)
    assert restricted_note(None) is None
