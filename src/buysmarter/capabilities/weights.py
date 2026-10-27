"""Axis weights for a query: the base weights plus the active capabilities' axes and shifts.

One rule for every category (ADR-0005): add each capability's axis weight, add its shifts
of the base weights, clamp at zero, normalize the sum to 100. Composition reproduces the
old composition table exactly (ingredient +25, brand -15, specs -5, popularity -5).
"""

from collections.abc import Iterable

from buysmarter.capabilities.base import Capability

_TOTAL = 100.0


def combine(base: dict[str, float], capabilities: Iterable[Capability]) -> dict[str, float]:
    """``base`` with every capability's axis and shifts, clamped at 0 and summing to 100."""
    weights = dict(base)
    for capability in capabilities:
        if (axis_name := capability.axis_name) is not None:
            weights[axis_name] = weights.get(axis_name, 0.0) + capability.current_axis_weight()
        for axis, shift in capability.weight_shifts.items():
            weights[axis] = weights.get(axis, 0.0) + shift
    clamped = {axis: max(weight, 0.0) for axis, weight in weights.items()}
    total = sum(clamped.values())
    if total <= 0:
        return dict(base)
    return {axis: weight * _TOTAL / total for axis, weight in clamped.items()}
