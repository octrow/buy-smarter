"""Exclude capability: substances the goods must not contain ("без сульфатов"), a request one.

No LLM: each known substance has concrete needles (sulfates are SLS, SLES and their INCI
and Russian names, not a bare "сульфат": "магния сульфат" is a vitamin), and a needle
right after "без", "не содержит", "free of" or before "-free" does not count. A product
whose composition text names the substance is rejected; one without a composition text
gets a con, and a title that says "без X" a pro. "без X" stays in the marketplace search,
but leaves the required features, the text composition reads its substances from and the
phrase the relevance filter judges ("has every property" would make it a requirement).
"""

import re
from typing import Any, NamedTuple

from buysmarter.capabilities.base import Capability, CapabilityContext
from buysmarter.knowledge.ingredients import composition_text
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import Candidate, QueryConstraints


class _Substance(NamedTuple):
    stem: str  # how a query names it ("без сульфатов", "sulfate-free")
    needles: str  # how a composition names it
    genitive: str
    accusative: str


_KNOWN = {
    "сульфаты": _Substance(
        r"сульфат|sulfate|sulphate|sle?s(?!\w)",
        r"sodium\s+laur(?:yl|eth)\s+sulfate|лаур(?:ил|ет)\s*сульфат|(?<!\w)sle?s(?!\w)",
        "сульфатов",
        "сульфаты",
    ),
    "парабены": _Substance(
        r"парабен|paraben", r"(?<!\w)\w*(?:paraben|парабен)", "парабенов", "парабены"
    ),
    "сахар": _Substance(
        r"сахар|sugar", r"(?<!\w)(?:сахар(?!озамен)|sucrose|sugar)", "сахара", "сахар"
    ),
    "лактоза": _Substance(r"лактоз|lactose", r"(?<!\w)(?:лактоз|lactose)", "лактозы", "лактозу"),
}
_NEGATION_BEFORE = re.compile(
    r"(?<!\w)(?:без|не\s+содерж\w*|free\s+(?:from|of))(?!\w)[^.;,]*$", re.IGNORECASE
)
_FREE_AFTER = re.compile(r"[\w]*[\s\-]*free(?!\w)", re.IGNORECASE)
_WINDOW = 30
# "без сульфатов", "безлактозный", "бессульфатный", "sulfate-free", "free of parabens".
# A list after "без": "без X, Y и Z", "без X и без Y".
# The parse drops commas ("без сульфатов, парабенов" -> "без сульфатов парабенов"), so a bare
# space also continues the list, but only before a known substance.
_SUBSTANCE_WORD = "(?=(?:" + "|".join(k.stem for k in _KNOWN.values()) + r")\w*)"
_LIST = r"\w+(?:(?:\s*,\s*|\s+(?:и|или|and|or)\s+)(?:без\s+)?\w+|\s+" + _SUBSTANCE_WORD + r"\w+)*"
_QUERY_RE = re.compile(
    rf"(?<!\w)бе[зс][\s-]?(?P<a>{_LIST})|(?P<b>\w+)[\s-]free(?!\w)|(?<!\w)free\s+of\s+(?P<c>{_LIST})",
    re.IGNORECASE,
)
_LIST_SPLIT = re.compile(r"\s*,\s*|\s+(?:и|или|and|or)\s+(?:без\s+)?|\s+", re.IGNORECASE)
_REJECT = "excluded substance: {name}"
_UNCHECKED_CON = "состав не проверен на {name}"
_FREE_PRO = "без {name} по названию"


def _canonical(word: str) -> str | None:
    """The known substance a word names ("сульфатов", "SLS"), or None."""
    return next((name for name, s in _KNOWN.items() if re.match(s.stem, word, re.IGNORECASE)), None)


def excluded_in(text: str) -> list[str]:
    """Known substances ``text`` excludes, in order ("без сульфатов" -> "сульфаты")."""
    found = []
    for match in _QUERY_RE.finditer(text):
        for name in _listed(match):
            if name not in found:
                found.append(name)
    return found


def _listed(match: re.Match[str]) -> list[str]:
    """The known substances one "без X, Y и Z" phrase names."""
    words = _LIST_SPLIT.split(match["a"] or match["b"] or match["c"])
    return [name for word in words if (name := _canonical(word)) is not None]


def _needles(name: str) -> str:
    known = _KNOWN.get(name)
    return known.needles if known else rf"(?<!\w){re.escape(name.lower())}"


def contains(text: str, name: str) -> bool:
    """True when ``text`` names substance ``name`` outside a negation."""
    for match in re.finditer(_needles(name), text, re.IGNORECASE):
        before = text[max(0, match.start() - _WINDOW) : match.start()]
        if _NEGATION_BEFORE.search(before) or _FREE_AFTER.match(text, match.end()):
            continue
        return True
    return False


def _says_free(text: str, name: str) -> bool:
    return name in excluded_in(text)


def strip_exclusions(text: str, names: list[str]) -> str:
    """``text`` without its "без X" / "X-free" phrases for the substances ``names``."""

    def drop(match: re.Match[str]) -> str:
        words = _LIST_SPLIT.split(match["a"] or match["b"] or match["c"])
        named = set(_listed(match)) | {w.lower() for w in words}
        return " " if named & set(names) else match[0]

    return " ".join(_QUERY_RE.sub(drop, text).split())


def _forms(name: str) -> tuple[str, str]:
    known = _KNOWN.get(name)
    return (known.genitive, known.accusative) if known else (name, name)


class Exclude(Capability):
    """Reject goods whose composition names an excluded substance."""

    name = "exclude"
    title = "Без веществ"
    level = "request"

    def setting(
        self, profile: ResolvedProfile, constraints: QueryConstraints
    ) -> CapabilitySetting | None:
        return CapabilitySetting(set_by="fallback") if constraints.exclude_substances else None

    def query(self, ctx: CapabilityContext, constraints: QueryConstraints) -> QueryConstraints:
        """Exclusions from the parse and the text; their phrases out of the features."""
        parsed = [_canonical(n) or n.lower() for n in constraints.exclude_substances]
        text = " ".join([ctx.query, constraints.search_phrase, *constraints.required_features])
        names = list(dict.fromkeys([*parsed, *excluded_in(text)]))
        if not names:
            return constraints
        features = [f for f in constraints.required_features if strip_exclusions(f, names)]
        product_type = strip_exclusions(constraints.product_type, names)
        return constraints.model_copy(
            update={
                "exclude_substances": names,
                "required_features": features,
                "product_type": product_type or constraints.product_type,
            }
        )

    def reject_content(self, ctx: CapabilityContext, candidate: Candidate) -> str | None:
        composition = composition_text(candidate.specs)
        for name in ctx.constraints.exclude_substances:
            if composition and contains(composition, name):
                return _REJECT.format(name=name)
        return None

    def notes(
        self,
        ctx: CapabilityContext,
        state: Any,
        candidate: Candidate,
        pros: list[str],
        cons: list[str],
    ) -> tuple[list[str], list[str]]:
        composition = composition_text(candidate.specs)
        for name in ctx.constraints.exclude_substances:
            genitive, accusative = _forms(name)
            if _says_free(candidate.title, name):
                pros = [*pros, _FREE_PRO.format(name=genitive)]
            elif not composition:
                cons = [*cons, _UNCHECKED_CON.format(name=accusative)]
        return pros, cons
