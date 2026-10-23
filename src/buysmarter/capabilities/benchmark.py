"""Benchmark capability: a performance index for the model, only with a provider (Kimovil).

It only enriches: the provider writes model specs in the marketplace's wording (which
the feature check then reads) and ``benchmark_index``; the core averages the index into
the specs axis without knowing where it came from. A profile that names benchmark
switches it on only with a provider from ``BENCHMARK_PROVIDERS`` (its own parameter, or
the provider whose rule takes the product type); without a profile setting the rule
alone decides (phase 3, decision 9).
"""

from typing import Any

from buysmarter.capabilities.base import Capability, CapabilityContext
from buysmarter.capabilities.benchmark_providers import (
    BENCHMARK_PROVIDERS,
    PROVIDER_KEY,
    provider_for,
)
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import Candidate, QueryConstraints
from buysmarter.services.collect import stage

BENCHMARK = "benchmark"


def _product_type(constraints: QueryConstraints) -> str:
    return constraints.product_type.strip() or constraints.search_phrase


class Benchmark(Capability):
    """A provider's index and model specs (Kimovil for phones)."""

    name = BENCHMARK
    title = "Бенчмарк"

    def label(self, params: dict[str, Any]) -> str:
        """ "Бенчмарк Kimovil": the title and the provider's name, when it is known."""
        provider = BENCHMARK_PROVIDERS.get(params.get(PROVIDER_KEY, ""))
        return f"{self.title} {provider.label}" if provider else self.title

    def setting(
        self, profile: ResolvedProfile, constraints: QueryConstraints
    ) -> CapabilitySetting | None:
        """The profile decides when it names benchmark; else the providers' rule."""
        stored = profile.capabilities.get(BENCHMARK)
        if stored is None:
            stored = CapabilitySetting(set_by="fallback")
        elif not stored.on:
            return None
        provider = stored.params.get(PROVIDER_KEY) or provider_for(_product_type(constraints))
        if provider not in BENCHMARK_PROVIDERS:
            return None
        return stored.model_copy(update={"params": {**stored.params, PROVIDER_KEY: provider}})

    async def enrich(self, ctx: CapabilityContext, pool: list[Candidate]) -> None:
        provider = ctx.params[PROVIDER_KEY]
        with stage(provider):
            await BENCHMARK_PROVIDERS[provider].enrich(pool)
