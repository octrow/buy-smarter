"""How the core runs capabilities: switch them on for a query, then call each hook at its point.

``activate`` picks the capabilities that apply (the profile for category capabilities,
the constraints for request ones) in registry order; ``ActiveCapabilities`` gives the
core one call per hook point and turns the ranking hooks into the plain ``RankHooks``
``rank()`` takes. Only hooks a capability overrides are called.
"""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from buysmarter.capabilities.base import Capability, CapabilityContext, Rejected, overrides
from buysmarter.capabilities.weights import combine
from buysmarter.knowledge.profile import ResolvedProfile
from buysmarter.ranking.engine import DEFAULT_WEIGHTS
from buysmarter.ranking.filters import apply_hard_filters
from buysmarter.ranking.models import (
    AxisScore,
    Candidate,
    PoolScoring,
    QueryConstraints,
    RankHooks,
    Recommendation,
)

Active = list[tuple[Capability, CapabilityContext]]


class ActiveCapabilities:
    """The capabilities on for one query, each with its context."""

    def __init__(self, constraints: QueryConstraints, items: Active) -> None:
        self.constraints = constraints
        self.items = [(cap, replace(ctx, hard_filters=self.hard_filters)) for cap, ctx in items]

    def _with(self, hook: str) -> Active:
        return [(cap, ctx) for cap, ctx in self.items if overrides(cap, hook)]

    def searches(self) -> list[tuple[str, list[str]]]:
        """Every extra Kaspi search, as ``(query, title needles)``."""
        return [s for cap, ctx in self._with("searches") for s in cap.searches(ctx)]

    def protected(self, pool: list[Candidate]) -> set[str]:
        """Candidates no relevance pass may drop."""
        return {i for cap, ctx in self._with("relevance") for i in cap.relevance(ctx, pool)}

    def reject_listing(self, text: str) -> str | None:
        """Why a listing is out (OLX ads, official-store offers), or None."""
        for cap, ctx in self._with("reject_listing"):
            if (reason := cap.reject_listing(ctx, text)) is not None:
                return reason
        return None

    async def enrich(self, pool: list[Candidate]) -> None:
        for cap, ctx in self._with("enrich"):
            await cap.enrich(ctx, pool)

    def hard_filters(self, pool: list[Candidate]) -> tuple[list[Candidate], Rejected]:
        """The core's hard filters with every active capability's checks."""
        return apply_hard_filters(pool, self.constraints, self.rank_hooks())

    def rank_hooks(self) -> RankHooks:
        """Weights, checks and scoring for ``rank()``, as plain functions."""
        weighted = [cap for cap, _ in self.items if cap.axis_name or cap.weight_shifts]
        return RankHooks(
            weights=combine(DEFAULT_WEIGHTS, weighted) if weighted else None,
            reject_listing=tuple(
                _bind(c, x, "reject_listing") for c, x in self._with("reject_listing")
            ),
            hard_features=tuple(
                _bind(c, x, "hard_features") for c, x in self._with("hard_features")
            ),
            reject_content=tuple(
                _bind(c, x, "reject_content") for c, x in self._with("reject_content")
            ),
            scoring=tuple(_scoring(c, x) for c, x in self.items if _scores(c)),
        )

    def flags(self, result: Recommendation) -> dict[str, bool]:
        """Result flags from every capability's report section."""
        flags: dict[str, bool] = {}
        for cap, ctx in self._with("section"):
            section = cap.section(ctx, result)
            if section is not None:
                flags.update(section.flags)
        return flags


def _bind(cap: Capability, ctx: CapabilityContext, hook: str) -> Callable[[Any], Any]:
    method = getattr(cap, hook)
    return lambda value: method(ctx, value)


def _scores(cap: Capability) -> bool:
    return any(overrides(cap, hook) for hook in ("prepare", "axis", "notes"))


def _scoring(cap: Capability, ctx: CapabilityContext) -> Callable[[list[Candidate]], PoolScoring]:
    """Prepare the capability's state over the passed pool, then bind its axis and notes."""

    def build(passed: list[Candidate]) -> PoolScoring:
        state = cap.prepare(ctx, passed)

        def axis(candidate: Candidate, weights: dict[str, float]) -> AxisScore | None:
            return cap.axis(ctx, state, candidate, weights)

        def notes(
            candidate: Candidate, pros: list[str], cons: list[str]
        ) -> tuple[list[str], list[str]]:
            return cap.notes(ctx, state, candidate, pros, cons)

        return PoolScoring(
            axis=axis if cap.axis_name and overrides(cap, "axis") else None,
            notes=notes if overrides(cap, "notes") else None,
        )

    return build


def read_query(
    registry: dict[str, Capability], constraints: QueryConstraints, *, query: str, use_llm: bool
) -> QueryConstraints:
    """Every capability's ``query`` hook, in registry order, before anything is switched on.

    The profile is not resolved yet at this point (its key is the category the hooks may
    rewrite), so the context carries an empty one.
    """
    profile = ResolvedProfile(slug="")
    for cap in registry.values():
        if overrides(cap, "query"):
            ctx = CapabilityContext(profile, constraints, use_llm=use_llm, query=query)
            constraints = cap.query(ctx, constraints)
    return constraints


def activate(
    registry: dict[str, Capability],
    profile: ResolvedProfile,
    constraints: QueryConstraints,
    *,
    query: str,
    use_llm: bool,
) -> ActiveCapabilities:
    """The capabilities that apply to this query, in registry order."""
    items: Active = []
    for cap in registry.values():
        setting = cap.setting(profile, constraints)
        if setting is None:
            continue
        ctx = CapabilityContext(profile, constraints, dict(setting.params), use_llm, query)
        items.append((cap, ctx))
    return ActiveCapabilities(constraints, items)
