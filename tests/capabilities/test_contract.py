"""The contract attributes every capability names: its title, level and Jev question."""

from buysmarter.capabilities import REGISTRY
from buysmarter.capabilities.base import Capability


def test_a_capability_without_attributes_is_a_category_one_with_no_question() -> None:
    assert Capability.jev_question is None
    assert Capability.level == "category"


def test_every_registered_capability_has_a_russian_title() -> None:
    for cap in REGISTRY.values():
        assert cap.title, cap.name
        assert any("а" <= ch <= "я" for ch in cap.title.lower()), cap.title


def test_audience_and_size_are_request_capabilities() -> None:
    levels = {name: cap.level for name, cap in REGISTRY.items()}
    # phase 4 adds request capabilities (fit, dims, ...): only these four are pinned here
    assert {n: levels[n] for n in ("audience", "size", "benchmark", "composition")} == {
        "audience": "request",
        "size": "request",
        "benchmark": "category",
        "composition": "category",
    }
