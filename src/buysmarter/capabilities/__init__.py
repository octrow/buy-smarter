"""Capabilities: optional ways of judging offers, plugged into one core flow (ADR-0005).

``REGISTRY`` order is the order the core calls them in at every hook point.
"""

from buysmarter.capabilities.base import Capability
from buysmarter.capabilities.composition import Composition

REGISTRY: dict[str, Capability] = {cap.name: cap for cap in (Composition(),)}
