"""Official stores: brand sites from the brand book as extra candidates, a category capability.

On by default; learning or the user switches it off for a category. The store offers go through
the same listing check as every other listing (``ctx.reject_listing``: another audience, another
tyre or mattress size), so a rejection a request capability adds reaches the stores by itself.
"""

from buysmarter.capabilities.base import CapabilityContext, DefaultOn
from buysmarter.ranking.models import Candidate
from buysmarter.services.official_stores import official_candidates


class OfficialStores(DefaultOn):
    """Brand sites as extra candidates, within the budget and the shared listing check."""

    name = "official_stores"
    title = "Официальные магазины"
    level = "category"

    async def candidates(self, ctx: CapabilityContext) -> list[Candidate]:
        book, constraints = ctx.profile.brand_book, ctx.constraints
        if ctx.reject_listing is None:  # the flow always sets it; a bare context may not
            return await official_candidates(book, constraints)
        return await official_candidates(book, constraints, ctx.reject_listing)
