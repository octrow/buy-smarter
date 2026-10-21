"""The core's English report vocabulary (axes, roles, pros/cons, axis explanations) in Russian.

The core keeps English for the CLI and MCP; the GUI translates what it shows. A phrase the table
does not know passes through unchanged, so a new core wording degrades to English, never to "".
"""

import re
from collections.abc import Callable

from buysmarter.gui.prefs_fields import SOURCE_LABELS, kzt
from buysmarter.ranking.models import Candidate

AXIS_LABELS = {
    "brand": "Бренд",
    "price": "Цена",
    "reviews": "Отзывы",
    "specs": "Характеристики",
    "popularity": "Популярность",
    "availability": "Наличие",
    "ingredient": "Состав",
}
BEST_ROLE = "Лучший выбор"
ROLE_LABELS = {"best_value": "Выгоднее", "best_budget": "Дешевле", "best_premium": "Премиум"}

_STRONG = {
    "brand": "надёжный бренд",
    "price": "выгодная цена",
    "reviews": "хорошие отзывы",
    "specs": "подходит по характеристикам",
    "popularity": "популярный",
    "availability": "в наличии",
    "ingredient": "нужный состав",
}
_WEAK = {
    "brand": "неизвестный бренд",
    "price": "дорого для рынка",
    "reviews": "слабые отзывы",
    "specs": "характеристики не подтверждены",
    "popularity": "мало покупок",
    "availability": "нет в наличии",
    "ingredient": "состав не подтверждён",
}
_REFERENCE = {"trusted-brand": "по известным брендам", "pool": "по всем предложениям"}


def axis_label(name: str) -> str:
    return AXIS_LABELS.get(name, name)


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def pro_con(text: str) -> str:
    """``strong price`` / ``weak reviews`` (``ranking.engine._pros_cons``) in plain words."""
    kind, _, axis = text.partition(" ")
    table = {"strong": _STRONG, "weak": _WEAK}.get(kind)
    return table.get(axis, text) if table else text


def _price(m: re.Match[str]) -> str:
    reference = _REFERENCE.get(m["label"], m["label"])
    return f"{kzt(int(m['price']))} при медиане {kzt(int(m['median']))} {reference} (×{m['ratio']})"


def _popularity(m: re.Match[str]) -> str:
    return f"{m['n']} отзывов — {m['pct']}% от лидера {source_label(m['src'])} ({m['max']})"


# Ordered: a longer phrase before the shorter one it contains ("out of stock" / "in stock").
_PHRASES: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    (re.compile(r"unknown brand — no trust data"), "бренд неизвестен, данных о доверии нет"),
    (re.compile(r"(?P<b>.+) is a tier-(?P<t>\w) brand"), r"\g<b> — бренд уровня \g<t>"),
    (re.compile(r"invalid price"), "некорректная цена"),
    (
        re.compile(
            r"price (?P<price>\d+) vs (?P<label>[\w-]+) median (?P<median>\d+) "
            r"\(ratio (?P<ratio>[\d.]+)\)"
        ),
        _price,
    ),
    # The core's own Russian note repeats what the median label already says.
    (re.compile(r" \(нет товаров с известным брендом — референс по всему пулу\)"), ""),
    (re.compile(r"no rating/review data available"), "нет рейтинга и отзывов"),
    (re.compile(r"rating (?P<r>[\d.]+)/5 across (?P<n>\d+) reviews"), r"\g<r>/5, отзывов: \g<n>"),
    (re.compile(r" \(discounted: unverified brand\)"), " (снижено: бренд не проверен)"),
    (re.compile(r" \(discounted: tier-(?P<t>\w) brand\)"), r" (снижено: бренд уровня \g<t>)"),
    (
        re.compile(r"no required features specified; spec data present"),
        "требования не заданы — ось нейтральна (характеристики есть)",
    ),
    (
        re.compile(r"no required features specified; no spec data"),
        "требования не заданы — ось нейтральна (характеристик нет)",
    ),
    (
        re.compile(r"(?P<m>\d+)/(?P<n>\d+) required features matched"),
        r"совпало требований: \g<m> из \g<n>",
    ),
    (re.compile(r"single candidate — no pool comparison"), "один кандидат — не с кем сравнить"),
    (re.compile(r"no review-count data for popularity"), "нет числа отзывов"),
    (re.compile(r"no popularity data across pool"), "нет данных о популярности"),
    (
        re.compile(r"(?P<n>\d+) reviews = (?P<pct>\d+)% of (?P<src>\w+) pool max \((?P<max>\d+)\)"),
        _popularity,
    ),
    (re.compile(r"out of stock"), "нет в наличии"),
    (re.compile(r"in stock"), "в наличии"),
    (re.compile(r"review trust (?P<t>[\d.]+)"), r"доверие к отзывам \g<t>"),
    (re.compile(r"(?P<p>\d+)% five-star"), r"\g<p>% пятёрок"),
    (re.compile(r"dates clustered"), "отзывы пачкой в одни даты"),
    (re.compile(r"mostly empty text"), "в основном пустые тексты"),
    (re.compile(r"(?P<p>\d+)% junk reviews"), r"\g<p>% мусорных отзывов"),
    (re.compile(r"(?P<p>\d+)% unverified purchases"), r"\g<p>% без подтверждённой покупки"),
    (re.compile(r"reviews look organic"), "отзывы выглядят естественными"),
    (re.compile(r"no reviews to assess"), "нет отзывов для проверки"),
]


def explain(text: str) -> str:
    """An axis explanation in Russian; unknown fragments stay as the core wrote them."""
    for pattern, repl in _PHRASES:
        text = pattern.sub(repl, text)
    return text


_REPEATED_FIRST_WORD = re.compile(r"^(\w+)\s+(?=\1\b)", re.IGNORECASE)


def clean_title(title: str) -> str:
    """Drop a doubled first word: sellers prepend the category, "Кроссовки кроссовки серый"."""
    cleaned = _REPEATED_FIRST_WORD.sub("", title, count=1)
    return cleaned[:1].upper() + cleaned[1:] if cleaned != title else title


# The fields ``ranking.engine._confidence`` counts, in the user's words.
_CONFIDENCE_FIELDS: list[tuple[str, Callable[[Candidate], bool]]] = [
    ("рейтинг", lambda c: c.rating is not None),
    ("число отзывов", lambda c: c.review_count is not None),
    ("известный бренд", lambda c: c.brand_tier is not None),
    ("ссылка на товар", lambda c: c.url is not None),
    ("характеристики", lambda c: bool(c.specs)),
]


def confidence_hint(candidate: Candidate) -> str:
    """What the confidence figure is made of, and what is missing."""
    missing = [name for name, has in _CONFIDENCE_FIELDS if not has(candidate)]
    head = "Уверенность — доля данных, по которым удалось оценить товар."
    return f"{head}\nНе хватает: {', '.join(missing)}." if missing else head
