"""Dims capability: the tyre or mattress size the query names, a request capability.

Only tyres and mattresses (clothing and shoes are the size capability). The size is
normalized ("205/55R16" and "205/55 Р16" are "205/55 R16"; "200 на 160" is "160x200"),
searched for, and kept out of the required features. A listing whose title names another
size of the same kind is rejected (main pool, OLX ads, official-store offers), as is a
product whose size specs (width, length, diameter; never "Размер упаковки") say another
size; a listing that names no size gets a con. Tyres or a mattress without a size: the
capability asks for it before the search.
"""

import re
from typing import Any

from buysmarter.capabilities.base import Capability, CapabilityContext, Question
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import Candidate, QueryConstraints

TYRE, MATTRESS = "tyre", "mattress"
_TYRE_RE = re.compile(
    r"(?<!\d)(\d{3})\s*/\s*(\d{2})\s*/?\s*Z?[RР]\s*(\d{2})C?(?!\d)", re.IGNORECASE
)
_MATTRESS_RE = re.compile(
    r"(?<![\dxх×*])(\d{2,3})\s*(?:[xх×*]|на|/)\s*(\d{3})(?![\dxх×*])", re.IGNORECASE
)
# Goods that need a size, by word start: "шина" but not "машина", "резина" not "резинка".
_NEEDS_SIZE = {
    TYRE: re.compile(r"(?<!\w)(?:авто)?шин[аыу]?(?!\w)|(?<!\w)резин[аыу]\b|(?<!\w)покрыш", re.I),
    MATTRESS: re.compile(r"(?<!\w)матрас", re.IGNORECASE),
}
_QUESTIONS = {
    TYRE: Question("Какой размер шин? Напишите его, например 205/55 R16."),
    MATTRESS: Question(
        "Какой размер матраса?", options=["90×200", "140×200", "160×200", "180×200"]
    ),
}
_REJECT = "other size: {size}"
_UNVERIFIED_CON = "размер не проверен: {size}"
_NUMBER_RE = re.compile(r"\d+")


def _tyre(match: re.Match[str]) -> str:
    return f"{match[1]}/{match[2]} R{match[3]}"


def _mattress(match: re.Match[str]) -> str:
    short, long = sorted((int(match[1]), int(match[2])))
    return f"{short}x{long}"


def _sizes(text: str) -> dict[str, list[str]]:
    """Every size ``text`` names, by kind."""
    return {
        TYRE: [_tyre(m) for m in _TYRE_RE.finditer(text)],
        MATTRESS: [_mattress(m) for m in _MATTRESS_RE.finditer(text)],
    }


def dims_in(text: str) -> dict[str, str]:
    """The first size of each kind ``text`` names, normalized."""
    return {kind: found[0] for kind, found in _sizes(text).items() if found}


def _without_sizes(text: str) -> str:
    return " ".join(_MATTRESS_RE.sub(" ", _TYRE_RE.sub(" ", text)).split())


def _needed(constraints: QueryConstraints) -> list[str]:
    text = f"{constraints.product_type} {constraints.search_phrase}"
    return [kind for kind, pattern in _NEEDS_SIZE.items() if pattern.search(text)]


def _first_number(value: str | None) -> int | None:
    match = _NUMBER_RE.search(value or "")
    return int(match[0]) if match else None


def _spec(specs: dict[str, str], *stems: str) -> int | None:
    """The number in the first spec whose key holds every stem."""
    for key, value in specs.items():
        if all(stem in key.lower() for stem in stems):
            return _first_number(value)
    return None


def _spec_size(kind: str, specs: dict[str, str]) -> str | None:
    """The size the product's own size specs give, or None."""
    if kind == MATTRESS:
        if found := dims_in(specs.get("Размер", "")).get(MATTRESS):
            return found
        width, length = _spec(specs, "ширин"), _spec(specs, "длин")
        if width is None or length is None:
            return None
        return f"{min(width, length)}x{max(width, length)}"
    width, height, rim = _spec(specs, "ширин"), _spec(specs, "высот"), _spec(specs, "диаметр")
    return f"{width}/{height} R{rim}" if None not in (width, height, rim) else None


class Dims(Capability):
    """Search for the size, reject another one, ask when it is missing."""

    name = "dims"
    title = "Размер шин и матрасов"
    level = "request"

    def setting(
        self, profile: ResolvedProfile, constraints: QueryConstraints
    ) -> CapabilitySetting | None:
        on = constraints.dims or _needed(constraints)
        return CapabilitySetting(set_by="fallback") if on else None

    def query(self, ctx: CapabilityContext, constraints: QueryConstraints) -> QueryConstraints:
        """The size from the parse or the text, normalized; searched; out of the features."""
        parsed = {k: dims_in(v).get(k, v) for k, v in constraints.dims.items()}
        read = dims_in(f"{ctx.query} {constraints.search_phrase}")
        # only for goods that take such a size: "монитор 27 на 120" is no mattress
        dims = {**{k: v for k, v in read.items() if k in _needed(constraints)}, **parsed}
        if not dims:
            return constraints
        phrase = " ".join([_without_sizes(constraints.search_phrase), *dims.values()])
        features = [f for f in constraints.required_features if not dims_in(f)]
        product_type = _without_sizes(constraints.product_type) or constraints.product_type
        return constraints.model_copy(
            update={
                "dims": dims,
                "search_phrase": phrase.strip(),
                "product_type": product_type,
                "required_features": features,
            }
        )

    def reject_listing(self, ctx: CapabilityContext, text: str) -> str | None:
        found = _sizes(text)
        for kind, wanted in ctx.constraints.dims.items():
            if found[kind] and wanted not in found[kind]:
                return _REJECT.format(size=found[kind][0])
        return None

    def reject_content(self, ctx: CapabilityContext, candidate: Candidate) -> str | None:
        for kind, wanted in ctx.constraints.dims.items():
            size = _spec_size(kind, candidate.specs)
            if size is not None and size != wanted:
                return _REJECT.format(size=size)
        return None

    def notes(
        self,
        ctx: CapabilityContext,
        state: Any,
        candidate: Candidate,
        pros: list[str],
        cons: list[str],
    ) -> tuple[list[str], list[str]]:
        found = _sizes(candidate.title)
        for kind, wanted in ctx.constraints.dims.items():
            if wanted not in found[kind] and _spec_size(kind, candidate.specs) != wanted:
                cons = [*cons, _UNVERIFIED_CON.format(size=wanted)]
        return pros, cons

    def clarify(self, ctx: CapabilityContext) -> Question | None:
        missing = [k for k in _needed(ctx.constraints) if k not in ctx.constraints.dims]
        return _QUESTIONS[missing[0]] if missing else None
