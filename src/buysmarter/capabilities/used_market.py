"""Used market: the category's switch for OLX and the used section, a category capability.

On by default. Switched off (by learning or by the user: "гречка без б/у") it only takes OLX
out of the marketplaces; collecting, filtering and both used sections ("На OLX" next to a
ranking and with no new pick) stay in the core. A used query ("б/у ноутбук") keeps OLX
whatever the switch: the request outranks the category (``capabilities.used_only``).
"""

from buysmarter.capabilities.base import CapabilityContext, DefaultOn
from buysmarter.capabilities.used_only import OLX, USED


class UsedMarket(DefaultOn):
    """Keep OLX among the marketplaces unless the category switched the used market off."""

    name = "used_market"
    title = "Рынок б/у"
    level = "category"

    def sources(self, ctx: CapabilityContext, selected: list[str]) -> list[str]:
        if ctx.constraints.condition == USED or self.setting(ctx.profile, ctx.constraints):
            return selected
        return [s for s in selected if s != OLX]
