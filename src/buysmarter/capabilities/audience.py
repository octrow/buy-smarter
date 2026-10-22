"""Audience capability: who the product is for ("мужские кроссовки"), a request capability.

Marketplace search ignores the audience word, so a listing marked for another audience
is rejected: one check the core applies to the main pool, to OLX ads and to official-store
offers (before their per-store cut). The query's audience comes from the parser, or from
the query text by regex when the parser missed it (``ranking.audience``).
"""

from buysmarter.capabilities.base import Capability, CapabilityContext
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.audience import other_audience, query_audience
from buysmarter.ranking.models import QueryConstraints


def query_text(query: str, constraints: QueryConstraints) -> str:
    """The text a request capability reads: the raw query, the search phrase, the features."""
    return " ".join([query, constraints.search_phrase, *constraints.required_features])


class Audience(Capability):
    """Reject listings marked for another audience."""

    name = "audience"
    title = "Для кого"
    level = "request"

    def setting(
        self, profile: ResolvedProfile, constraints: QueryConstraints
    ) -> CapabilitySetting | None:
        return CapabilitySetting(set_by="fallback") if constraints.gender else None

    def query(self, ctx: CapabilityContext, constraints: QueryConstraints) -> QueryConstraints:
        if not constraints.gender and (
            gender := query_audience(query_text(ctx.query, constraints))
        ):
            return constraints.model_copy(update={"gender": gender})
        return constraints

    def reject_listing(self, ctx: CapabilityContext, text: str) -> str | None:
        gender = ctx.constraints.gender
        other = other_audience(gender, text) if gender else None
        return f"other audience: {other}" if other is not None else None
