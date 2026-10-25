"""``buysmarter prefs``: the saved search settings, as the app's «Настройки поиска» (parity #9)."""

import asyncio

import pytest
from typer.testing import CliRunner

from buysmarter.cli import main
from buysmarter.cli import prefs as prefs_cli
from buysmarter.memory import prefs

runner = CliRunner()


def _prefs(*args: str):
    return runner.invoke(main.app, ["prefs", *args])


def _stored() -> dict:
    return asyncio.run(prefs.get_all())


def test_nothing_saved_shows_every_setting_in_russian_with_its_default() -> None:
    result = _prefs()

    assert result.exit_code == 0
    for label in ("Город по умолчанию", "Бюджет", "Маркетплейсы", "Исключить бренды"):
        assert label in result.stdout
    assert "без лимита" in result.stdout and "все" in result.stdout


@pytest.mark.parametrize(
    ("key", "value", "stored", "shown"),
    [
        ("city", " Астана ", "Астана", "Астана"),
        ("budget", "40 000", 40000, "40\xa0000 ₸"),
        ("marketplaces", "Kaspi, olx", ["kaspi", "olx"], "Kaspi, OLX"),
        ("excluded_brands", "Noname,  Generic ,", ["Noname", "Generic"], "Noname, Generic"),
    ],
)
def test_set_stores_what_the_app_stores_and_shows_it(key, value, stored, shown) -> None:
    result = _prefs("set", key, value)

    assert result.exit_code == 0
    assert prefs_cli.SAVED in result.stdout
    assert _stored()[key] == stored  # the app's form reads these types
    assert shown in _prefs().stdout


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("budget", "сорок тысяч", "число"),
        ("marketplaces", "kaspi, avito", "avito"),
        ("marketplaces", " , ", prefs_cli.NO_SOURCE),
    ],
)
def test_a_wrong_value_is_refused_with_the_reason_and_nothing_is_stored(key, value, reason) -> None:
    result = _prefs("set", key, value)

    assert result.exit_code != 0
    assert reason in result.output
    assert key not in _stored()


def test_an_unknown_key_is_refused() -> None:
    assert _prefs("set", "colour", "red").exit_code != 0


@pytest.mark.parametrize(
    ("key", "cleared"), [("city", ""), ("budget", 0), ("marketplaces", []), ("excluded_brands", [])]
)
def test_clear_puts_a_setting_back_to_its_default(key, cleared) -> None:
    _prefs("set", "city", "Астана")

    result = _prefs("clear", key)

    assert result.exit_code == 0
    assert _stored()[key] == cleared


def test_a_store_that_cannot_be_written_says_so(monkeypatch) -> None:
    async def down(key, value):
        raise OSError("disk full at /secret/path")

    monkeypatch.setattr(prefs, "set", down)

    result = _prefs("set", "city", "Астана")

    assert result.exit_code == 1
    assert prefs_cli.STORE_DOWN in result.stdout
    assert "/secret/path" not in result.stdout
