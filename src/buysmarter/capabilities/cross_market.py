"""Cross-market comparison: offers side by side by landed cost, a category capability.

Jev switches it on for clothes and shoes (CONTEXT.md "Cross-market comparison"); for any
other category the query's words do ("где дешевле", "сравни площадки", "из Китая"), and
the words leave the search phrase. The comparison itself (one row per marketplace, priced
to the door) is the core's: ``services.recommend`` builds it when this capability is on.
"""

import re

from buysmarter.capabilities.base import Capability, CapabilityContext
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import QueryConstraints

CROSS_MARKET = "cross_market"
_WORDS_RE = re.compile(
    r"(?<!\w)(?:где\s+дешевле|сравни(?:ть)?\s+площадки|из\s+китая)(?!\w)", re.IGNORECASE
)


class CrossMarket(Capability):
    """Compare marketplaces by landed cost, delivery route and authenticity level."""

    name = CROSS_MARKET
    title = "Сравнение площадок"
    level = "category"
    jev_question = "Is it clothing, footwear or a fashion accessory?"

    def setting(
        self, profile: ResolvedProfile, constraints: QueryConstraints
    ) -> CapabilitySetting | None:
        if constraints.cross_market:  # the request outranks the category, as "б/у" does
            return CapabilitySetting(set_by="fallback")
        return profile.enabled(self.name)

    def query(self, ctx: CapabilityContext, constraints: QueryConstraints) -> QueryConstraints:
        """On by the query's words; the words leave the search phrase and the product type."""
        if _WORDS_RE.search(ctx.query) is None:
            return constraints
        phrase, product_type = (
            " ".join(_WORDS_RE.sub(" ", text).split())
            for text in (constraints.search_phrase, constraints.product_type)
        )
        return constraints.model_copy(
            update={
                "cross_market": True,
                "search_phrase": phrase or constraints.search_phrase,
                "product_type": product_type or constraints.product_type,
            }
        )
