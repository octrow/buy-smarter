"""Benchmark capability: a performance index for the model (Kimovil for phones).

It only enriches: the provider (``services.phone_specs``) writes model specs in the
marketplace's wording (which the feature check then reads) and ``benchmark_index``;
the core averages the index into the specs axis without knowing where it came from.
Switched on by the profile; without a profile setting, by today's rule: a phone stem in
the category's first word (phase 1, decision 14).
"""

from buysmarter.capabilities.base import Capability, CapabilityContext
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import Candidate, QueryConstraints
from buysmarter.services.collect import stage
from buysmarter.services.phone_specs import enrich_with_kimovil

BENCHMARK = "benchmark"
# The category's first word names the product: "чехол для телефона" is a case.
_SMARTPHONE_STEMS = ("смартфон", "телефон", "айфон", "iphone", "smartphone")


def is_smartphone_category(category: str) -> bool:
    words = category.lower().split()
    return bool(words) and any(stem in words[0] for stem in _SMARTPHONE_STEMS)


class Benchmark(Capability):
    """Kimovil's index and model specs for phones."""

    name = BENCHMARK
    title = "Бенчмарк"

    def setting(
        self, profile: ResolvedProfile, constraints: QueryConstraints
    ) -> CapabilitySetting | None:
        """The profile decides when it names benchmark; else the phone-stem fallback."""
        if BENCHMARK in profile.capabilities:
            return profile.enabled(BENCHMARK)
        if is_smartphone_category(constraints.search_phrase):
            return CapabilitySetting(set_by="fallback")
        return None

    async def enrich(self, ctx: CapabilityContext, pool: list[Candidate]) -> None:
        with stage("kimovil"):
            await enrich_with_kimovil(pool)
