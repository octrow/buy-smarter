"""Capabilities: optional ways of judging offers, plugged into one core flow (ADR-0005).

``REGISTRY`` order is the order the core calls them in at every hook point.
"""

from buysmarter.capabilities.audience import Audience
from buysmarter.capabilities.base import Capability
from buysmarter.capabilities.benchmark import Benchmark
from buysmarter.capabilities.composition import Composition
from buysmarter.capabilities.dims import Dims
from buysmarter.capabilities.exclude import Exclude
from buysmarter.capabilities.fit import Fit
from buysmarter.capabilities.official_stores import OfficialStores
from buysmarter.capabilities.original_only import OriginalOnly
from buysmarter.capabilities.size import Size
from buysmarter.capabilities.used_market import UsedMarket
from buysmarter.capabilities.used_only import UsedOnly

REGISTRY: dict[str, Capability] = {
    cap.name: cap
    for cap in (
        Audience(),
        Size(),
        Fit(),
        Dims(),
        Exclude(),
        OriginalOnly(),
        UsedOnly(),
        UsedMarket(),
        OfficialStores(),
        Benchmark(),
        Composition(),
    )
}
