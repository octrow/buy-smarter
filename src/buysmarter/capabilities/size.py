"""Size capability: the clothing or shoe size the query asks for, a request capability.

Marketplace cards do not list the sizes in stock, so a size is never a filter: it leaves
the search phrase (full-text search ANDs "42 размер" away) and the required features, and
a listing whose title does not name it gets an "unverified" con.
"""

from typing import Any

from buysmarter.capabilities.audience import query_text
from buysmarter.capabilities.base import Capability, CapabilityContext
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.audience import has_size, is_apparel, is_size_phrase, query_size, strip_size
from buysmarter.ranking.models import Candidate, QueryConstraints

_SIZE_UNVERIFIED_CON = "размер {size}: наличие не проверено"


class Size(Capability):
    """Read the size out of the query; flag listings that do not name it."""

    name = "size"

    def setting(
        self, profile: ResolvedProfile, constraints: QueryConstraints
    ) -> CapabilitySetting | None:
        return CapabilitySetting(set_by="fallback") if constraints.size else None

    def query(self, ctx: CapabilityContext, constraints: QueryConstraints) -> QueryConstraints:
        """Size from the text, out of the search phrase; size phrases out of apparel features."""
        text = query_text(ctx.query, constraints)
        updates: dict[str, Any] = {}
        if constraints.size is None and (size := query_size(text)):
            updates["size"] = size
        if query_size(constraints.search_phrase):
            phrase = constraints.search_phrase
            updates["search_phrase"] = strip_size(phrase) or phrase
        features = [
            f for f in constraints.required_features if not (is_apparel(text) and is_size_phrase(f))
        ]
        if len(features) != len(constraints.required_features):
            updates["required_features"] = features
        return constraints.model_copy(update=updates) if updates else constraints

    def notes(
        self,
        ctx: CapabilityContext,
        state: Any,
        candidate: Candidate,
        pros: list[str],
        cons: list[str],
    ) -> tuple[list[str], list[str]]:
        size = ctx.constraints.size
        if size and not has_size(candidate.title, size):
            return pros, [*cons, _SIZE_UNVERIFIED_CON.format(size=size)]
        return pros, cons
