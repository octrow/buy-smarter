"""select_sources: one validation of marketplace names for the CLI, MCP and the HTTP API."""

import pytest

from buysmarter.services.recommend import ALL_SOURCES, select_sources


def test_names_are_normalized_and_keep_their_order() -> None:
    assert select_sources([" OLX", "kaspi ", ""]) == ["olx", "kaspi"]


def test_empty_selection_means_every_allowed_source() -> None:
    assert select_sources([]) == list(ALL_SOURCES)
    assert select_sources([], allowed=("kaspi", "olx")) == ["kaspi", "olx"]


def test_unknown_or_disallowed_names_are_rejected_by_name() -> None:
    with pytest.raises(ValueError, match="amazon"):
        select_sources(["kaspi", "amazon"])
    with pytest.raises(ValueError, match="ozon"):
        select_sources(["ozon"], allowed=("kaspi", "olx"))


def test_duplicates_are_dropped() -> None:
    assert select_sources(["kaspi", "KASPI", "olx"]) == ["kaspi", "olx"]
