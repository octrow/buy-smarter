"""Request understanding after the parse: restricted goods (and, later, clarifications).

Restricted goods are searched with a warning, never refused (CONTEXT.md "Restricted
category"). The LLM parse sets ``restricted``; without it a static word list still flags
alcohol, tobacco and vapes and weapons by whole word, but never prescription medicines.
"""

import re

from buysmarter.ranking.models import Restricted

# Whole words only: "винный бокал" and "кальянный уголь" are not the goods themselves.
_RESTRICTED_RES: dict[Restricted, re.Pattern[str]] = {
    "tobacco": re.compile(
        r"(?<!\w)(?:вейп\w*|жиж[аиуе]|электронн\w* сигарет\w*|сигарет\w*|табак\w*|"
        r"кальян(?:а|ы|ов)?)(?!\w)",
        re.IGNORECASE,
    ),
    "alcohol": re.compile(
        r"(?<!\w)(?:вин[оа]|пив[оа]|водк\w*|коньяк\w*|виски|алкогол\w*)(?!\w)", re.IGNORECASE
    ),
    "weapon": re.compile(r"(?<!\w)(?:травмат(?!олог)\w*|нож\w*-бабочк\w*)(?!\w)", re.IGNORECASE),
}
# Goods for (not of) a restricted thing: a glass for wine, coal for a hookah.
_STOP_CONTEXT_RE = re.compile(
    r"(?:бокал\w*|кружк\w*|шкаф\w*|угол\w*|штопор\w*)\s+для\s+\w+|безалкогольн\w*\s+\w+",
    re.IGNORECASE,
)

_RESTRICTED_NOTES: dict[Restricted, str] = {
    "rx": "Рецептурный препарат: аптека может продать его только по рецепту.",
    "alcohol": "Алкоголь: продажа онлайн и доставка ограничены законом, покупка с 21 года.",
    "tobacco": "Табак и вейпы: продажа и доставка ограничены законом, покупка с 21 года.",
    "weapon": "Оружие: покупка может требовать разрешения, доставка ограничена.",
}


def restricted_kind(text: str) -> Restricted | None:
    """The restricted group the static list finds in ``text`` (no prescription check)."""
    text = _STOP_CONTEXT_RE.sub(" ", text)
    for kind, pattern in _RESTRICTED_RES.items():
        if pattern.search(text):
            return kind
    return None


def restricted_note(kind: Restricted | None) -> str | None:
    """The warning shown before the results for a restricted group."""
    return _RESTRICTED_NOTES[kind] if kind is not None else None
