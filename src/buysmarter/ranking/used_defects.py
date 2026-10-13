"""Defects a seller admits to in an OLX used listing's own text.

Pure, no I/O. Category-agnostic: only defects that read the same for a phone, a
water flosser or a vacuum. A match is ignored when one of the three words right before
it negates it ("без трещин", "не залит") or one of the three words right after does
("ремонта не было", "сколов нет"). Negation is checked within one clause only, so
"царапин нет, но экран треснут" still flags the crack.

# ponytail: keyword regexes, swap for an LLM classifier if misses or false hits show up.
"""

import re

_DEFECTS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in (
        ("трещины/сколы", r"трещин|треснут|разбит|\bскол(?:ы|ов|ом|а)?\b"),
        ("заявлена неисправность", r"не\s?(?:работа|включа|заряжа)|сломан|неисправ|broken"),
        ("был залит", r"залит|попала\s+вода|утопил"),
        (
            "слабый аккумулятор",
            r"(?:батаре|аккумулятор)\w*\W+(?:не\s?держ|слаб|плох|умер)|не\s?держит\s+заряд|быстро\s+разряжа",
        ),
        ("был в ремонте", r"ремонт|вскрывал|\bменяли|замен[аеуы]\b"),
    )
)
_FOR_PARTS = re.compile(r"на\s+(?:запчаст|детали)|for\s+parts", re.IGNORECASE)
_CLAUSE_SPLIT = re.compile(r"[.,;:!?\n]+")
_NEGATION = re.compile(r"^(?:без|нет|не|ни|no|not|without)$", re.IGNORECASE)
_WORD = re.compile(r"\w+")
_NEGATION_WINDOW = 3  # words checked on each side: covers "без трещин и сколов"


def is_for_parts(text: str) -> bool:
    """True when the listing sells the item for parts: not a working product."""
    return bool(_FOR_PARTS.search(text))


def find_defects(text: str) -> list[str]:
    """Labels of the defects ``text`` admits to, in table order, each once."""
    clauses = [c for c in _CLAUSE_SPLIT.split(text) if c.strip()]
    return [
        label for label, pattern in _DEFECTS if any(_stated(pattern, clause) for clause in clauses)
    ]


def _stated(pattern: re.Pattern[str], clause: str) -> bool:
    return any(not _negated(clause, m) for m in pattern.finditer(clause))


def _negated(clause: str, match: re.Match[str]) -> bool:
    before = _WORD.findall(clause[: match.start()])[-_NEGATION_WINDOW:]
    # The match itself may run into a word ("трещин" in "трещинами"): skip its tail.
    after = _WORD.findall(re.sub(r"^\w*", "", clause[match.end() :]))[:_NEGATION_WINDOW]
    return any(_NEGATION.match(word) for word in before + after)
