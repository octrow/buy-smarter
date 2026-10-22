"""Benchmark as a capability: switched on by the profile, else the providers' rule."""

import pytest
from market_sources.kimovil.parse import KimovilPhone
from market_sources.kimovil.source import KimovilSource

from buysmarter.capabilities.base import CapabilityContext
from buysmarter.capabilities.benchmark import BENCHMARK, Benchmark
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import Candidate, QueryConstraints

_NO_PROFILE = ResolvedProfile(slug="x")


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("смартфон", True),
        ("смартфон iPhone 13", True),
        ("телефон Samsung", True),
        ("айфон", True),
        ("ирригатор", False),
        ("iPhone 15 Pro", True),
        ("чехол для телефона", False),
    ],
)
def test_without_a_profile_setting_the_providers_rule_switches_it_on(category, expected):
    setting = Benchmark().setting(_NO_PROFILE, QueryConstraints(search_phrase=category))

    assert (setting is not None) is expected
    if setting is not None:
        assert setting.set_by == "fallback"


def test_the_profile_setting_wins_over_the_fallback() -> None:
    off = ResolvedProfile(slug="x", capabilities={BENCHMARK: CapabilitySetting(enabled=False)})
    user_on = CapabilitySetting(params={"provider": "kimovil"}, user=True)
    on = ResolvedProfile(slug="x", capabilities={BENCHMARK: user_on})

    assert Benchmark().setting(off, QueryConstraints(search_phrase="смартфон")) is None
    assert Benchmark().setting(on, QueryConstraints(search_phrase="часы")) is not None


async def test_enrich_writes_the_index_and_names_its_source(monkeypatch) -> None:
    async def _lookup(self, query):
        return KimovilPhone(full_name="Samsung Galaxy S23", slug="s23", url="https://k", ki=85.0)

    monkeypatch.setattr(KimovilSource, "lookup", _lookup)
    phone = Candidate(source="kaspi", source_product_id="1", title="Samsung Galaxy S23", price=1)
    ctx = CapabilityContext(
        _NO_PROFILE, QueryConstraints(search_phrase="смартфон"), {"provider": "kimovil"}
    )

    await Benchmark().enrich(ctx, [phone])

    assert phone.benchmark_index == 85.0
    assert phone.benchmark_label == "Kimovil"
