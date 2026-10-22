"""The budget a query names, by rules that never read a unit of measure as money.

Plan phase 2, decision 10, in order: another currency means no budget (just a note);
tenge marks money; thousands slang ("тыс", "косарь", "мың", "штук" after "до/за/бюджет")
multiplies by 1000; "N k" is money only from 10 up or after "до/за" ("4K" is a screen);
a range sets both ends; a bare "до N" is money only without a unit or a fraction after it,
and under 1000 means thousands. Numbers apart are never glued, except digit groups
("15 000"). The money words leave the search phrase.
"""

import re
from dataclasses import dataclass

FOREIGN_CURRENCY_NOTE = "бюджет в другой валюте не учтён"
_THOUSAND = 1000
_MILLION = 1_000_000
# "N k" below this is not money: "4K" names a screen resolution.
_MIN_K_AMOUNT = 10

# A whole number, digit groups allowed ("15 000", "15.000"); not part of a word, code or
# fraction. The lookahead also refuses a following digit, so backtracking cannot leave
# "1" of "18.5" as a number.
_NUM = r"(?<![\w.,/])(?P<{}>\d{{1,3}}(?:[ \u00a0.,]\d{{3}})+|\d+)(?![.,]?\d)"
# Russian and Kazakh, with Kazakh case endings ("теңгеге", "мыңға").
_KZT = r"(?:₸|тг(?!\w)|тенге\w*|теңге\w*)"
_SLANG = r"(?:тыс\w*\.?|тыщ\w*|косар\w*|мың\w*)"
_MILLION_WORD = r"(?:млн\.?|миллион\w*)"
_MULT = rf"(?P<mult>{_SLANG}|{_MILLION_WORD}|штук\w*|[kк](?!\w))"
# Kazakh "15000 теңгеге дейін": a postposition meaning "до".
_POST = r"(?P<post>\s*дейін(?!\w))?"
_PREP = r"(?P<prep>\b(?:до|за|бюджет\w*)\s+)"

_FOREIGN_RE = re.compile(
    rf"(?:\b(?:до|за)\s+)?(?:\$\s*{_NUM.format('a')}"
    rf"|{_NUM.format('b')}\s*(?:\$|usd(?!\w)|€|eur(?!\w)|руб(?:л[ьяей]\w*|\.)?(?!\w)|₽))",
    re.IGNORECASE,
)
_RANGE_TAIL = rf"\s*{_MULT}?\s*(?P<kzt>{_KZT})?"
# "от 30 до 40 тыс" or "30-40 тыс"; a bare "N до M" is a model or size number and a budget
# ("iPhone 15 до 500 тыс"), never a range.
_RANGE_RES = (
    re.compile(
        rf"\bот\s+{_NUM.format('low')}\s+до\s+{_NUM.format('high')}{_RANGE_TAIL}", re.IGNORECASE
    ),
    re.compile(
        rf"{_NUM.format('low')}\s*[-–—]\s*{_NUM.format('high')}{_RANGE_TAIL}", re.IGNORECASE
    ),
)
_AMOUNT_RE = re.compile(
    rf"{_PREP}?{_NUM.format('n')}\s*{_MULT}?\s*(?P<kzt>{_KZT})?{_POST}", re.IGNORECASE
)
_BARE_SLANG_RE = re.compile(r"\b(?:до\s+|за\s+)?косар\w*", re.IGNORECASE)
# After a bare "до N": a unit of measure, so the number is not money.
_UNIT_RE = re.compile(
    r'\s*(?:"|(?:м|см|мм|кг|г|л|мл|лет|год\w*|мес\w*|ме|вт|w|дюйм\w*|гб|тб|размер\w*'
    r"|шт\w*|мач)(?!\w))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Budget:
    """The budget read from a query and the query without its money words."""

    text: str
    budget_max: int | None = None
    budget_min: int | None = None
    budget_note: str | None = None


def _number(raw: str) -> int:
    return int(re.sub(r"\D", "", raw))


def _cut(query: str, match: re.Match[str]) -> str:
    return " ".join(f"{query[: match.start()]} {query[match.end() :]}".split())


def _scale(mult: str | None) -> int:
    """What a multiplier word means: thousands, or millions for «млн»."""
    if not mult:
        return 1
    return _MILLION if re.fullmatch(_MILLION_WORD, mult, re.IGNORECASE) else _THOUSAND


def _range(query: str) -> Budget | None:
    matches = (match for pattern in _RANGE_RES for match in pattern.finditer(query))
    for match in matches:
        mult = match["mult"]
        if not (mult or match["kzt"]) or (mult and mult.lower().startswith("штук")):
            continue
        scale = _scale(mult)
        low, high = _number(match["low"]) * scale, _number(match["high"]) * scale
        if low < high:
            return Budget(_cut(query, match), budget_max=high, budget_min=low)
    return None


def _amount_value(query: str, match: re.Match[str]) -> int | None:
    """The money a single amount means, or None when it is not money."""
    value, mult, prep = _number(match["n"]), match["mult"], (match["prep"] or "").strip().lower()
    if match["post"]:
        prep = "до"
    if mult:
        word = mult.lower()
        if word.startswith("штук") and not prep:
            return None
        if word in {"k", "к"}:
            return value * _THOUSAND if value >= _MIN_K_AMOUNT or prep in {"до", "за"} else None
        return value * _scale(mult)
    if match["kzt"]:
        return value
    if prep != "до" or _UNIT_RE.match(query, match.end()):
        return None
    return value * _THOUSAND if value < _THOUSAND else value


def parse_budget(query: str) -> Budget:
    """Budget fields of ``query`` and the query with the money words cut out."""
    if (foreign := _FOREIGN_RE.search(query)) is not None:
        return Budget(_cut(query, foreign), budget_note=FOREIGN_CURRENCY_NOTE)
    if (ranged := _range(query)) is not None:
        return ranged
    for match in _AMOUNT_RE.finditer(query):
        if (value := _amount_value(query, match)) is not None:
            return Budget(_cut(query, match), budget_max=value)
    if (slang := _BARE_SLANG_RE.search(query)) is not None:
        return Budget(_cut(query, slang), budget_max=_THOUSAND)
    return Budget(" ".join(query.split()))
