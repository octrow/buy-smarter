"""Shared test doubles."""

from dataclasses import dataclass, field
from typing import Any

from market_sources.base import RawOffer, RawProduct

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.flow import activate
from buysmarter.knowledge.ingredients import ActiveSubstance, IngredientBook
from buysmarter.knowledge.profile import COMPOSITION, CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import QueryConstraints, RankHooks


def composition_profile(book: IngredientBook | None = None) -> ResolvedProfile:
    """A profile with composition on (adapalene by default)."""
    book = book or IngredientBook(substances=[ActiveSubstance(canonical="адапален")])
    params = {"ingredient_book": book.model_dump(mode="json")}
    return ResolvedProfile(
        slug="test", capabilities={COMPOSITION: CapabilitySetting(params=params)}
    )


def capability_hooks(
    constraints: QueryConstraints, profile: ResolvedProfile | None = None
) -> RankHooks:
    """What the flow hands ``rank()`` for this profile (none by default) and constraints."""
    profile = profile or ResolvedProfile(slug="test")
    return activate(REGISTRY, profile, constraints, query="", use_llm=False).rank_hooks()


def composition_hooks(constraints: QueryConstraints) -> RankHooks:
    """What the flow hands ``rank()`` for a composition category and these constraints."""
    return capability_hooks(constraints, composition_profile())


@dataclass
class FakeSource:
    """In-memory marketplace: per-query cards (``main`` otherwise), specs and offers."""

    name: str
    main: list[RawProduct]
    by_query: dict[str, list[RawProduct]] = field(default_factory=dict)
    spec_map: dict[str, dict[str, str]] = field(default_factory=dict)
    offer_map: dict[str, list[RawOffer]] = field(default_factory=dict)

    async def search(self, query: str, city: str | None = None) -> list[RawProduct]:
        return list(self.by_query.get(query, self.main))

    async def specs(self, product_id: str) -> dict[str, str]:
        return dict(self.spec_map.get(product_id, {}))

    async def offers(self, product_id: str) -> list[RawOffer]:
        return list(self.offer_map.get(product_id, []))

    async def reviews(self, product_id: str) -> list[Any]:
        return []
