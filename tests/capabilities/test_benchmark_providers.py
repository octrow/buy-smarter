"""Benchmark only with a provider: Kimovil for phones, and the rule without an LLM (decision 9)."""

import pytest

from buysmarter.capabilities.benchmark import BENCHMARK, Benchmark
from buysmarter.capabilities.benchmark_providers import (
    BENCHMARK_PROVIDERS,
    PROVIDER_KEY,
    provider_for,
)
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import QueryConstraints


@pytest.mark.parametrize(
    "product_type",
    [
        "смартфон",
        "смартфоны",
        "мобильный телефон",
        "телефон Samsung",
        "смартфон iPhone 13",
        "iPhone 15",
        "iPhone 15 Pro",
        "айфон",
    ],
)
def test_a_phone_gets_kimovil(product_type: str) -> None:
    assert provider_for(product_type) == "kimovil"


@pytest.mark.parametrize(
    "product_type",
    [
        "чехол на iPhone 15",
        "чехол для телефона",
        "держатель телефона",
        "радиотелефон",
        "кнопочный телефон",
        "стационарный телефон",
        "телефонный кабель",
        "ноутбук",
        "ирригатор",
        "",
    ],
)
def test_an_accessory_or_another_product_gets_no_provider(product_type: str) -> None:
    assert provider_for(product_type) is None


def test_every_provider_asks_jev_its_own_question() -> None:
    assert all(p.jev_question for p in BENCHMARK_PROVIDERS.values())


def _profile(**setting: object) -> ResolvedProfile:
    return ResolvedProfile(
        slug="x", capabilities={BENCHMARK: CapabilitySetting.model_validate(setting)}
    )


def test_a_profile_naming_benchmark_switches_it_on_only_with_a_provider() -> None:
    laptop = QueryConstraints(search_phrase="ноутбук для учёбы", product_type="ноутбук")
    phone = QueryConstraints(search_phrase="смартфон", product_type="смартфон")

    assert Benchmark().setting(_profile(set_by="jev"), laptop) is None
    named = Benchmark().setting(_profile(params={PROVIDER_KEY: "kimovil"}), laptop)
    assert named is not None and named.params[PROVIDER_KEY] == "kimovil"
    unknown = _profile(params={PROVIDER_KEY: "geekbench"})
    assert Benchmark().setting(unknown, phone) is None
    guessed = Benchmark().setting(_profile(set_by="jev"), phone)
    assert guessed is not None and guessed.params[PROVIDER_KEY] == "kimovil"


def test_the_rule_reads_the_product_type_not_the_search_phrase() -> None:
    case = QueryConstraints(search_phrase="чехол на iPhone 15", product_type="чехол")

    assert Benchmark().setting(ResolvedProfile(slug="x"), case) is None
