"""Used only: "б/у ноутбук", a request capability.

``condition == "used"`` from the parse switches it on. OLX is added to the marketplaces
before they are opened (the caller need not name it), and every report shows the used
section first (``cli.report.used_first``).
"""

from buysmarter.capabilities.base import Capability, CapabilityContext
from buysmarter.knowledge.profile import CapabilitySetting, ResolvedProfile
from buysmarter.ranking.models import QueryConstraints

USED = "used"
OLX = "olx"


class UsedOnly(Capability):
    """Look for second-hand goods: bring in OLX, show its section first."""

    name = "used_only"
    title = "Только б/у"
    level = "request"

    def setting(
        self, profile: ResolvedProfile, constraints: QueryConstraints
    ) -> CapabilitySetting | None:
        return CapabilitySetting(set_by="fallback") if constraints.condition == USED else None

    def sources(self, ctx: CapabilityContext, selected: list[str]) -> list[str]:
        if self.setting(ctx.profile, ctx.constraints) is None or OLX in selected:
            return selected
        return [*selected, OLX]
