"""Preferences in the store, and the one-time import of the pre-store JSON file."""

import json
import os
from pathlib import Path

import pytest

from buysmarter.memory import prefs


class _StoreDown:
    async def __aenter__(self) -> None:
        raise OSError("unable to open database file")

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def legacy_file() -> Path:
    """The conftest points ``$BUYSMARTER_PREFS_FILE`` at a tmp path; start each test without it."""
    path = Path(os.environ["BUYSMARTER_PREFS_FILE"])
    path.parent.mkdir(parents=True, exist_ok=True)
    for leftover in path.parent.glob("prefs.json*"):
        leftover.unlink()
    return path


async def test_set_then_get_round_trips_through_the_store() -> None:
    await prefs.set("city", "Алматы")
    await prefs.set("budget", 40000)

    assert await prefs.get("city") == "Алматы"
    assert await prefs.get("missing") is None
    assert await prefs.get_all() == {"city": "Алматы", "budget": 40000}


async def test_reads_are_empty_when_the_store_is_down(monkeypatch, caplog) -> None:
    monkeypatch.setattr(prefs, "async_session", _StoreDown)

    with caplog.at_level("WARNING"):
        assert await prefs.get_all() == {}
        assert await prefs.get("city") is None
    assert "unavailable" in caplog.text


async def test_set_raises_when_the_store_is_down(monkeypatch) -> None:
    monkeypatch.setattr(prefs, "async_session", _StoreDown)

    with pytest.raises(OSError):
        await prefs.set("city", "Алматы")


async def test_legacy_import_adds_missing_keys_only_and_renames(legacy_file) -> None:
    await prefs.set("budget", 9000)
    legacy_file.write_text(json.dumps({"city": "Алматы", "budget": 5000}), encoding="utf-8")

    await prefs.import_legacy_file()

    assert await prefs.get_all() == {"city": "Алматы", "budget": 9000}
    assert not legacy_file.exists()
    assert legacy_file.with_name("prefs.json.imported").exists()


async def test_legacy_import_without_a_file_is_a_noop(legacy_file) -> None:
    await prefs.import_legacy_file()

    assert await prefs.get_all() == {}


@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
async def test_legacy_import_sets_aside_an_unreadable_file(legacy_file, caplog, content) -> None:
    legacy_file.write_text(content, encoding="utf-8")

    with caplog.at_level("WARNING"):
        await prefs.import_legacy_file()

    assert await prefs.get_all() == {}
    assert legacy_file.with_name("prefs.json.invalid").read_text(encoding="utf-8") == content
    assert "unreadable" in caplog.text


async def test_legacy_import_survives_a_failed_rename(legacy_file, monkeypatch, caplog) -> None:
    legacy_file.write_text(json.dumps({"city": "Алматы"}), encoding="utf-8")

    def _fail(self, target):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "rename", _fail)
    with caplog.at_level("WARNING"):
        await prefs.import_legacy_file()
        await prefs.import_legacy_file()  # the file is still there: a re-run imports nothing new

    assert await prefs.get_all() == {"city": "Алматы"}
    assert "Could not rename" in caplog.text


@pytest.mark.parametrize(
    ("stored", "items"),
    [
        (["Noname", " Generic ", ""], ["Noname", "Generic"]),
        ("Noname,  Generic ,", ["Noname", "Generic"]),
        (None, []),
        (5, []),
    ],
)
def test_as_list_reads_a_stored_list_or_a_comma_separated_string(stored, items) -> None:
    assert prefs.as_list(stored) == items


@pytest.mark.parametrize(
    ("stored", "budget"),
    [
        (40000, 40000),
        ("40 000", 40000),
        ("40\xa0000", 40000),
        (None, 0),
        ("abc", 0),
        (10**12, prefs.BUDGET_MAX),
    ],
)
def test_as_budget_reads_a_number_or_its_text_and_zero_is_no_limit(stored, budget) -> None:
    assert prefs.as_budget(stored) == budget


@pytest.mark.parametrize(
    ("stored", "sources"),
    [
        (["kaspi", "OLX", "kaspi"], ["kaspi", "olx"]),
        ("ozon, avito", ["ozon"]),
        ("avito", []),
        (None, []),
    ],
)
def test_as_sources_keeps_the_known_marketplaces_once(stored, sources) -> None:
    assert prefs.as_sources(stored) == sources


async def test_search_defaults_are_the_saved_marketplaces_and_excluded_brands(monkeypatch) -> None:
    async def get_all() -> dict[str, object]:
        return {"marketplaces": "olx, avito", "excluded_brands": ["Noname"], "city": "Астана"}

    monkeypatch.setattr(prefs, "get_all", get_all)

    assert await prefs.search_defaults() == (["olx"], ["Noname"])
