"""Capabilities: optional ways of judging offers, plugged into one core flow (ADR-0005).

``REGISTRY`` order is the order the core calls them in at every hook point.
"""

from buysmarter.capabilities.audience import Audience
from buysmarter.capabilities.base import Capability
from buysmarter.capabilities.benchmark import Benchmark
from buysmarter.capabilities.composition import Composition
from buysmarter.capabilities.fit import Fit
from buysmarter.capabilities.size import Size

REGISTRY: dict[str, Capability] = {
    cap.name: cap for cap in (Audience(), Size(), Fit(), Benchmark(), Composition())
}
