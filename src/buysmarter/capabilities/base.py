"""The capability contract: optional hooks the core calls at fixed points of one flow.

A capability is one optional way of judging offers (CONTEXT.md "Capability"): composition,
benchmark, audience, size. Category capabilities are switched on by the category profile,
request capabilities by the query. Every hook below is optional: the base class makes it
neutral, and the core calls only the hooks a capability overrides
(docs/plans/2026-10-22-capabilities-phase1.md, "Хуки и точки вызова").

``ranking/`` never imports this package: the flow turns the active capabilities into
plain weights, predicates and functions and passes those to ``rank()``.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import AxisScore, Candidate, QueryConstraints, Recommendation

Rejected = list[tuple[Candidate, str]]
# The core's hard filters with every active capability's predicates: (passed, rejected).
HardFilters = Callable[[list[Candidate]], tuple[list[Candidate], Rejected]]


@dataclass(frozen=True)
class CapabilityContext:
    """What a hook sees: the profile, the constraints so far, its parameters, the LLM switch.

    ``hard_filters`` is the core's own filter set, given to hooks that must know whether
    a pool would survive ranking (composition's "no product has the substance" check).
    """

    profile: ResolvedProfile
    constraints: QueryConstraints
    params: dict[str, Any] = field(default_factory=dict)
    use_llm: bool = True
    query: str = ""
    hard_filters: HardFilters | None = None


@dataclass(frozen=True)
class Section:
    """A capability's part of the report: warnings and the result flags it sets."""

    flags: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class Capability:
    """Base of every capability: identity, its axis and weight shifts, neutral hooks.

    ``axis_name`` names the score axis the capability adds (``None`` == no axis), worth
    ``axis_weight``; ``weight_shifts`` move base axis weights (``capabilities.weights``).
    """

    name: ClassVar[str] = ""
    axis_name: ClassVar[str | None] = None
    axis_weight: ClassVar[float] = 0.0
    weight_shifts: ClassVar[dict[str, float]] = {}

    def setting(
        self, profile: ResolvedProfile, constraints: QueryConstraints
    ) -> CapabilitySetting | None:
        """On for this query? A category capability reads the profile (the default)."""
        return profile.enabled(self.name)

    def query(self, ctx: CapabilityContext, constraints: QueryConstraints) -> QueryConstraints:
        """Read or rewrite the constraints after parsing, before any search (every capability)."""
        return constraints

    def searches(self, ctx: CapabilityContext) -> list[tuple[str, list[str]]]:
        """Extra Kaspi searches: ``(query, title needles)``; a hit must name a needle."""
        return []

    def relevance(self, ctx: CapabilityContext, pool: list[Candidate]) -> set[str]:
        """``source_product_id``s the relevance filter must not drop."""
        return set()

    def reject_listing(self, ctx: CapabilityContext, text: str) -> str | None:
        """Why a listing (title, category path, ad or store offer name) is out, or None."""
        return None

    def hard_features(self, ctx: CapabilityContext, features: list[str]) -> list[str]:
        """The required features the hard filter string-matches."""
        return features

    async def enrich(self, ctx: CapabilityContext, pool: list[Candidate]) -> None:
        """Add data to the deduped pool before ranking (may raise to stop the run)."""

    def reject_content(self, ctx: CapabilityContext, candidate: Candidate) -> str | None:
        """Why the product itself is out, checked after the required features, or None."""
        return None

    def prepare(self, ctx: CapabilityContext, passed: list[Candidate]) -> Any:
        """Pool context for ``axis`` and ``notes``, built from the candidates that passed."""
        return None

    def axis(
        self, ctx: CapabilityContext, state: Any, candidate: Candidate, weights: dict[str, float]
    ) -> AxisScore | None:
        """The capability's axis for one candidate (only with ``axis_name`` set)."""
        return None

    def notes(
        self,
        ctx: CapabilityContext,
        state: Any,
        candidate: Candidate,
        pros: list[str],
        cons: list[str],
    ) -> tuple[list[str], list[str]]:
        """Pros and cons after the core's own; may replace cons."""
        return pros, cons

    def section(self, ctx: CapabilityContext, result: Recommendation) -> Section | None:
        """Warnings and flags for the report, after ranking."""
        return None

    def clarify(self, ctx: CapabilityContext) -> str | None:
        """A question to ask before searching (phase 2; nobody asks in phase 1)."""
        return None


def overrides(capability: Capability, hook: str) -> bool:
    """True when ``capability`` implements ``hook`` (the core skips neutral ones)."""
    return getattr(type(capability), hook) is not getattr(Capability, hook)
