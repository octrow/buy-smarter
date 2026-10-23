"""Original only: "Dyson только оригинал", a request capability.

The core already bars suspected fakes (brand mimicry, a counterfeit review verdict, a
suspicious price) from every role but keeps them in the ranking with a warning. With this
capability on (``drops_barred``) the core hides them after the review pass, counts them as
rejected and lists them in the report; when every candidate is suspected the run goes the
"no new pick" way with the OLX ads. "оригинал" leaves the search and the required features;
"оригинальный подарок" does not switch it on.
"""

import re

from buysmarter.capabilities.base import Capability, CapabilityContext
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import QueryConstraints

_ORIGINAL_RE = re.compile(r"(?<!\w)(?:только\s+)?оригинал(?!\w)", re.IGNORECASE)


class OriginalOnly(Capability):
    """Hide suspected fakes instead of flagging them."""

    name = "original_only"
    title = "Только оригинал"
    level = "request"
    drops_barred = True

    def setting(
        self, profile: ResolvedProfile, constraints: QueryConstraints
    ) -> CapabilitySetting | None:
        return CapabilitySetting(set_by="fallback") if constraints.original_only else None

    def query(self, ctx: CapabilityContext, constraints: QueryConstraints) -> QueryConstraints:
        """On by the parse or by "оригинал" in the text; the word leaves search and features."""
        on = constraints.original_only or _ORIGINAL_RE.search(ctx.query) is not None
        if not on:
            return constraints
        phrase, product_type = (
            " ".join(_ORIGINAL_RE.sub(" ", text).split())
            for text in (constraints.search_phrase, constraints.product_type)
        )
        features = [f for f in constraints.required_features if _ORIGINAL_RE.sub("", f).strip()]
        return constraints.model_copy(
            update={
                "original_only": True,
                "search_phrase": phrase or constraints.search_phrase,
                "product_type": product_type or constraints.product_type,
                "required_features": features,
            }
        )
