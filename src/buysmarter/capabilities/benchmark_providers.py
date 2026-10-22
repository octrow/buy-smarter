"""Benchmark providers: who can score a model, and for which product types (phase 3, decision 9).

Benchmark is on only with a provider from this registry: one Jev question per provider
(the classifier asks it on a new category) and a rule without an LLM over the product type.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from itertools import takewhile
from typing import Literal

from buysmarter.ranking.models import Candidate
from buysmarter.services.phone_specs import enrich_with_kimovil

PROVIDER_KEY = "provider"
# The names learning may return (an enum in its answer schema).
ProviderName = Literal["kimovil"]


@dataclass(frozen=True)
class BenchmarkProvider:
    """One benchmark source: its report label, its Jev question, its rule, its enrich step."""

    label: str
    jev_question: str
    matches: Callable[[str], bool]
    enrich: Callable[[list[Candidate]], Awaitable[None]]


# The product named before the first preposition: "чехол для телефона" is a case.
_PREPOSITIONS = frozenset({"для", "на", "к", "под", "от", "с"})
# Nominative singular and plural, whole words: "держатель телефона", "радиотелефон" are not phones.
_PHONE_WORDS = frozenset({"смартфон", "смартфоны", "телефон", "телефоны", "smartphone"})
# A brand line names a phone only as the first word: "чехол на iPhone 15" is a case.
_PHONE_FIRST_WORDS = frozenset({"iphone", "айфон", "айфоны"})
# Phones Kimovil does not rank: push-button and landline ones.
_NOT_SMARTPHONES = frozenset({"кнопочный", "стационарный"})


def is_phone_type(product_type: str) -> bool:
    """A phone itself, judged without an LLM by the words before the first preposition."""
    words = re.findall(r"[\w-]+", product_type.lower())
    head = list(takewhile(lambda word: word not in _PREPOSITIONS, words))
    if _NOT_SMARTPHONES.intersection(head):
        return False
    return bool(words) and (words[0] in _PHONE_FIRST_WORDS or any(w in _PHONE_WORDS for w in head))


# ponytail: a plain dict of one provider; a provider class per source when a second one
# (laptops, graphics cards, TVs) arrives.
BENCHMARK_PROVIDERS: dict[str, BenchmarkProvider] = {
    "kimovil": BenchmarkProvider(
        label="Kimovil",
        jev_question="Is this product type a smartphone or mobile phone itself, not an accessory?",
        matches=is_phone_type,
        enrich=enrich_with_kimovil,
    ),
}


def provider_for(product_type: str) -> str | None:
    """The provider whose rule takes this product type, or ``None``."""
    return next((key for key, p in BENCHMARK_PROVIDERS.items() if p.matches(product_type)), None)
