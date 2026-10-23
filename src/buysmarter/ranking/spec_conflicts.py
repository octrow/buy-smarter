"""Title-vs-spec unit conflicts (e.g. "10000mAh" in the title, "5000 мАч" in specs).

Conservative on purpose: only exact unit matches, only units the title states,
specs without a unit are ignored. Warnings are cons only; scores are unchanged.
"""

import re

# Raw unit (lowercased) -> canonical unit label shown to the user.
_UNIT_ALIASES: dict[str, str] = {
    "мач": "мАч",
    "mah": "мАч",
    "вт": "Вт",
    "w": "Вт",
    "мл": "мл",
    "ml": "мл",
    "л": "л",
    "гб": "ГБ",
    "gb": "ГБ",
    "тб": "ТБ",
    "tb": "ТБ",
    "дюйм": "дюйм",
    "дюйма": "дюйм",
    '"': "дюйм",
    "″": "дюйм",
    "мм": "мм",
    "см": "см",
    "кг": "кг",
    "г": "г",
}
# A number is either space-grouped thousands ("10 000") or a plain one with an
# optional decimal comma/dot.
_NUMBER_RE = r"\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_NUMBER_UNIT_RE = re.compile(
    r"("
    + _NUMBER_RE
    + r")\s*("
    + "|".join(sorted((re.escape(u) for u in _UNIT_ALIASES), key=len, reverse=True))
    + r")(?![^\W\d_])",
    re.IGNORECASE,
)
# A size chain ("160x200x22 см", "160х200 см"): the unit after the last side is every side's.
_SIZE_CHAIN_RE = re.compile(
    r"(?P<sides>\d+(?:[.,]\d+)?(?:\s*[xх×*]\s*\d+(?:[.,]\d+)?)+)\s*(?P<unit>см|мм)(?![^\W\d_])",
    re.IGNORECASE,
)
_SIDE_SPLIT_RE = re.compile(r"\s*[xх×*]\s*", re.IGNORECASE)
# Relative tolerance for "the same number" (rounding in marketing copy).
_NUMBER_TOLERANCE = 0.01
_CONFLICT_TEMPLATE = "название: {title} {unit}, характеристики: {spec} {unit}"


def _to_float(raw_num: str) -> float:
    return float(re.sub(r"[ \u00a0]", "", raw_num).replace(",", "."))


def _numbers_by_unit(text: str) -> list[tuple[str, float, str]]:
    """(raw number, value, canonical unit) for every number+unit pair in ``text``."""
    found = []
    text = _SIZE_CHAIN_RE.sub(
        lambda m: " ".join(f"{side} {m['unit']}" for side in _SIDE_SPLIT_RE.split(m["sides"])),
        text,
    )
    for raw_num, raw_unit in _NUMBER_UNIT_RE.findall(text):
        unit = _UNIT_ALIASES[raw_unit.lower()]
        found.append((raw_num, _to_float(raw_num), unit))
    return found


def _same_number(a: float, b: float) -> bool:
    return abs(a - b) <= _NUMBER_TOLERANCE * max(abs(a), abs(b))


def spec_conflicts(title: str, specs: dict[str, str]) -> list[str]:
    """Warnings for spec values whose number+unit contradicts the title's."""
    title_pairs = _numbers_by_unit(title)
    warnings: list[str] = []
    for spec_value in specs.values():
        for spec_raw, spec_val, unit in _numbers_by_unit(spec_value):
            same_unit = [(raw, val) for raw, val, u in title_pairs if u == unit]
            if not same_unit or any(_same_number(val, spec_val) for _, val in same_unit):
                continue
            warnings.append(
                _CONFLICT_TEMPLATE.format(title=same_unit[0][0], spec=spec_raw, unit=unit)
            )
    return warnings
