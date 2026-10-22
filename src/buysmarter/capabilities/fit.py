"""Fit capability: the device the goods are for ("чехол на iPhone 15"), a request capability.

The device is not the product: it goes into the search (marketplaces need it) but not into
the product type (the profile key stays "чехол"). The ranking cuts the score of a listing
that does not name the whole model ("iPhone 15 Pro" is not "iPhone 15"), through the
model-token penalty (``RankHooks.model_tokens``), and a listing that names it gets a pro.

Without an LLM the device is read from "для|на|под|к <target>": the target is Latin with a
digit or a known device word, never a number or a unit ("на 64 GB", "для лица").
"""

import re
from typing import Any

from buysmarter.capabilities.base import Capability, CapabilityContext
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.model_tokens import compact, names_model
from buysmarter.ranking.models import Candidate, QueryConstraints

_FITS_PRO = "подходит к {target}"
_FIT_RE = re.compile(r"(?<!\w)(?:для|на|под|к)\s+", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[\w+]+")
_LATIN_RE = re.compile(r"[a-z0-9+]+", re.IGNORECASE)
_HAS_LATIN_AND_DIGIT = re.compile(r"(?=.*[a-z])(?=.*\d)", re.IGNORECASE)
# Device words a target may start with, Cyrillic spellings mapped to the titles' Latin.
_DEVICE_WORDS = {
    "айфон": "iPhone",
    "айфона": "iPhone",
    "айфону": "iPhone",
    "айпад": "iPad",
    "самсунг": "Samsung",
    "сяоми": "Xiaomi",
    "ксиоми": "Xiaomi",
    "хуавей": "Huawei",
    "хонор": "Honor",
    "iphone": "iPhone",
    "ipad": "iPad",
    "samsung": "Samsung",
    "galaxy": "Galaxy",
    "xiaomi": "Xiaomi",
    "redmi": "Redmi",
    "poco": "POCO",
    "huawei": "Huawei",
    "honor": "Honor",
    "playstation": "PlayStation",
    "macbook": "MacBook",
    "airpods": "AirPods",
}
# A first word that is a unit or a socket, not a device ("на 64 GB", "лампа на E27").
_UNITS = frozenset({"gb", "tb", "mb", "mah", "w", "l", "mm", "cm", "kg", "ml", "v", "hz"})
_SOCKETS = re.compile(r"^(?:e\d{2}|gu\d{1,2}|g\d{1,2})$", re.IGNORECASE)
# A number glued to its unit ("65W", "20000mAh", "64GB") is a spec, not a device.
_GLUED_UNIT = re.compile(
    r"^\d+(?:[.,]\d+)?(?:gb|tb|mb|mah|w|wh|l|mm|cm|kg|ml|v|hz|гб|тб|мач|вт)$", re.IGNORECASE
)
_MAX_TARGET_WORDS = 5


def _device_word(token: str) -> str:
    return _DEVICE_WORDS.get(token.lower(), token)


def _target_words(rest: str) -> list[str]:
    """The leading Latin, digit or device words of ``rest``."""
    words = []
    for token in _TOKEN_RE.findall(rest)[:_MAX_TARGET_WORDS]:
        if token.lower() not in _DEVICE_WORDS and not _LATIN_RE.fullmatch(token):
            break
        words.append(_device_word(token))
    return words


def _is_target(words: list[str]) -> bool:
    if not words or words[0].isdigit():
        return False
    first = words[0].lower()
    if first in _UNITS or _SOCKETS.match(first) or _GLUED_UNIT.match(first):
        return False
    known = first in {w.lower() for w in _DEVICE_WORDS.values()}
    return known or _HAS_LATIN_AND_DIGIT.match(" ".join(words)) is not None


def fit_target(text: str) -> str | None:
    """The device after "для|на|под|к" in ``text``, or None."""
    for match in _FIT_RE.finditer(text):
        words = _target_words(text[match.end() :])
        if _is_target(words):
            return " ".join(words)
    return None


def _without_target(text: str, target: str) -> str:
    """``text`` without the target and the preposition before it."""
    words = [re.escape(w) for w in target.split()]
    pattern = rf"(?:(?<!\w)(?:для|на|под|к)\s+)?(?<!\w){r'\s*'.join(words)}(?!\w)"
    return " ".join(re.sub(pattern, " ", text, flags=re.IGNORECASE).split())


class Fit(Capability):
    """Search for the device; prefer listings that name the whole model."""

    name = "fit"
    title = "Совместимость"
    level = "request"

    def setting(
        self, profile: ResolvedProfile, constraints: QueryConstraints
    ) -> CapabilitySetting | None:
        return CapabilitySetting(set_by="fallback") if constraints.fit_target else None

    def query(self, ctx: CapabilityContext, constraints: QueryConstraints) -> QueryConstraints:
        """The target from the parse or the text; in the search phrase, out of the type."""
        target = constraints.fit_target or fit_target(ctx.query)
        if not target:
            return constraints
        target = " ".join(_device_word(w) for w in target.split())
        phrase = constraints.search_phrase
        if compact(target) not in compact(phrase):
            phrase = f"{phrase} {target}".strip()
        product_type = _without_target(constraints.product_type, target)
        return constraints.model_copy(
            update={
                "fit_target": target,
                "search_phrase": phrase,
                "product_type": product_type or constraints.product_type,
            }
        )

    def notes(
        self,
        ctx: CapabilityContext,
        state: Any,
        candidate: Candidate,
        pros: list[str],
        cons: list[str],
    ) -> tuple[list[str], list[str]]:
        target = ctx.constraints.fit_target or ""
        if target and names_model(" ".join([candidate.title, *candidate.specs.values()]), target):
            return [*pros, _FITS_PRO.format(target=target)], cons
        return pros, cons
