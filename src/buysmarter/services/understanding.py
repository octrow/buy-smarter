"""Request understanding after the parse: restricted goods and clarifications (ADR-0005).

Restricted goods are searched with a warning, never refused (CONTEXT.md "Restricted
category"). The LLM parse sets ``restricted``; without it a static word list still flags
alcohol, tobacco and vapes and weapons by whole word, but never prescription medicines.

A clarification replaces the ranking only on real ambiguity: an open request, a need several
product types solve, several products named, a service, an unreadable query, or a capability
missing a parameter. At most two rounds: the caller passes ``clarify_round``; on the last
round (or with ``no_clarify``) a question with options searches the first one and says so,
one without options ends in ``NothingToSearch``, a capability's question is dropped.
"""

import logging
import re
from typing import Literal, NoReturn

from pydantic import BaseModel, Field

from buysmarter.capabilities.base import Question
from buysmarter.llm.prompts import bare_product_type
from buysmarter.ranking.models import QueryConstraints, Restricted

logger = logging.getLogger(__name__)

ClarifyKind = Literal["open", "need", "multi", "capability", "unclear", "service"]
# Rounds 0 and 1 may ask; round 2 settles.
MAX_ROUNDS = 2
# Fewer options than this is no choice to ask about.
_MIN_CHOICE = 2

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


class Clarification(BaseModel):
    """A question the user answers before the search, with ready answers to pick from."""

    kind: ClarifyKind
    question: str
    options: list[str] = Field(default_factory=list)
    round: int = 0
    restricted_note: str | None = None


class NeedsClarification(Exception):  # noqa: N818 - an outcome, not an error
    """The request needs an answer first. Not a ``ValueError``: no entry may report it as
    "nothing to recommend", so each catches it above ``ValueError``."""

    def __init__(self, clarification: Clarification) -> None:
        self.clarification = clarification
        super().__init__(clarification.question)


class NothingToSearch(ValueError):
    """The last round left nothing to search: a service, or a request still unreadable."""


_QUESTIONS: dict[ClarifyKind, str] = {
    "open": "Что именно ищем? Выберите вариант или напишите свой.",
    "need": "Что из этого подойдёт? Выберите вариант или напишите свой.",
    "multi": "С чего начнём? Выберите один товар.",
    "service": "Похоже, это услуга, а не товар: я ищу только товары. Какой товар нужен?",
    "unclear": "Не понял, что искать. Напишите, какой товар нужен.",
}
_NOTHING: dict[ClarifyKind, str] = {
    "service": "Это услуга, а не товар: buy-smarter ищет только товары.",
    "unclear": "Не понял запрос: переформулируйте, какой товар нужен.",
}


def _semantic_kind(c: QueryConstraints) -> ClarifyKind | None:
    """What the parse leaves to ask, or None when the search can go ahead."""
    if c.intent in ("service", "unclear"):
        return c.intent
    if c.intent == "product":
        return None
    if c.intent == "open":
        return "open" if c.options else "unclear"
    if len(c.options) >= _MIN_CHOICE:
        return c.intent
    return None if c.search_phrase.strip() else "unclear"


def _ask(
    c: QueryConstraints, kind: ClarifyKind, options: list[str], clarify_round: int
) -> NoReturn:
    # ponytail: the LLM may ask too often; review these lines after 50 live queries and
    # tighten the prompt's "ask only when a search would be a guess" rule if it does.
    logger.info(
        "clarify: %s round %d options=%s for %r", kind, clarify_round, options, c.search_phrase
    )
    raise NeedsClarification(
        Clarification(
            kind=kind,
            question=_QUESTIONS[kind],
            options=options,
            round=clarify_round,
            restricted_note=restricted_note(c.restricted),
        )
    )


def choose(c: QueryConstraints, option: str) -> QueryConstraints:
    """The request narrowed to one product; budget, city and audience stay as parsed."""
    option = option.strip()
    return c.model_copy(
        update={
            "intent": "product",
            "search_phrase": option,
            "category": option,
            "product_type": bare_product_type(option),
            "options": [],
        }
    )


def settle_request(
    c: QueryConstraints, *, clarify_round: int, no_clarify: bool
) -> tuple[QueryConstraints, str | None]:
    """The constraints to search with and the option assumed for the user, if any.

    Raises ``NeedsClarification`` while rounds are left, ``NothingToSearch`` when the last
    round has no option to fall back on.
    """
    kind = _semantic_kind(c)
    if kind is None:
        return c, None
    options = c.options if kind in ("open", "need", "multi") else []
    if clarify_round < MAX_ROUNDS and not no_clarify:
        _ask(c, kind, options, clarify_round)
    if options:
        return choose(c, options[0]), options[0].strip()
    raise NothingToSearch(_NOTHING.get(kind, _NOTHING["unclear"]))


def apply_answer(c: QueryConstraints, answer: str) -> QueryConstraints | None:
    """The request narrowed to the option ``answer`` names; None for the user's own text."""
    wanted = answer.strip().casefold()
    for option in c.options:
        if option.strip().casefold() == wanted:
            return choose(c, option)
    return None


def ask_capability(
    question: Question | None, c: QueryConstraints, clarify_round: int, no_clarify: bool
) -> None:
    """Ask a capability's question while rounds are left; on the last one search without it."""
    if question is None or no_clarify or clarify_round >= MAX_ROUNDS:
        return
    raise NeedsClarification(
        Clarification(
            kind="capability",
            question=question.question,
            options=list(question.options),
            round=clarify_round,
            restricted_note=restricted_note(c.restricted),
        )
    )
