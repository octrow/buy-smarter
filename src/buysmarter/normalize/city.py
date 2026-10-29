"""One city normalization for the naive parse, the LLM's free string, ``--city`` and the saved one.

The cities are those the marketplaces know (``market_sources.cities``); a name comes back as its
Russian form ("Шымкент"), found in Latin or in a Russian case ("в Шымкенте", "из Караганды").
"""

import re

from market_sources.cities import KASPI_CITY_IDS

_RU_BY_ID = {cid: name for name, cid in KASPI_CITY_IDS.items() if not name.isascii()}
_NAMES = {name: _RU_BY_ID[cid] for name, cid in KASPI_CITY_IDS.items()} | {
    "нур-султан": "астана",
    "nur-sultan": "астана",
}
# a Russian case ending after the stem: «Шымкенте», «Караганды», «Костанае», «Астаной»
# ponytail: whole words with a fixed ending list; «для больших семей» still reads as Семей,
# a context check (a preposition before the name) if that ever shows up in real queries.
_ENDING = "(?:а|е|у|ы|и|я|ю|й|ой|ом|ем)?"


def _pattern(name: str) -> str:
    if name.isascii():
        return re.escape(name)
    stem = name[:-1] if name[-1] in "аыяй" else name
    return re.escape(stem) + _ENDING


_CITY_RES = [
    (re.compile(rf"(?<![\w-]){_pattern(name)}(?![\w-])", re.I), canon)
    for name, canon in _NAMES.items()
]


def find_city(text: str | None) -> str | None:
    """The first known city named in ``text`` ("Шымкент"), else None."""
    for city_re, canon in _CITY_RES:
        if text and city_re.search(text):
            return canon.title()
    return None


def strip_city(text: str) -> str:
    """``text`` without the city names in it."""
    for city_re, _ in _CITY_RES:
        text = city_re.sub(" ", text)
    return text
