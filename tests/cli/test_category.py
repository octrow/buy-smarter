"""``buysmarter category``: stored categories and their switches, as «Категории» (#11, #13)."""

import asyncio

from typer.testing import CliRunner

from buysmarter.capabilities.provenance import CHANGED_NOTE
from buysmarter.cli import main
from buysmarter.knowledge import generate as generate_module
from buysmarter.knowledge.brands import BrandBook
from buysmarter.knowledge.models_book import ModelBook, ModelEntry
from buysmarter.knowledge.profile import COMPOSITION, CapabilitySetting
from buysmarter.knowledge.store import load_knowledge, save_knowledge, update_knowledge

runner = CliRunner()


def _category(*args: str):
    return runner.invoke(main.app, ["category", *args])


def _stored_vacuum() -> None:
    brand = BrandBook(tiers={"Roborock": "A", "Xiaomi": "C"}, aliases={})
    model = ModelBook(entries=[ModelEntry(canonical="Roborock S8", brand="Roborock", codes=["S8"])])
    asyncio.run(save_knowledge("робот-пылесос", brand, model, "opus"))


def _user(slug: str, name: str) -> bool | None:
    raw = asyncio.run(load_knowledge(slug)) or {}
    stored = raw.get("capabilities", {}).get(name)
    return CapabilitySetting.model_validate(stored).user if stored else None


def test_list_names_each_stored_category_with_its_books_and_parent() -> None:
    _stored_vacuum()
    asyncio.run(update_knowledge("моющий-робот", {"parent": "робот-пылесос"}, "user"))

    result = _category("list")

    assert result.exit_code == 0, result.output
    out = " ".join(result.stdout.split())
    assert "робот-пылесос" in out and "моющий-робот" in out
    assert "бренды 2, модели 1" in out
    assert "из робот-пылесос" in out
    assert "user, 20" in out  # who, then the date


def test_list_says_so_when_nothing_is_learned() -> None:
    result = _category("list")

    assert result.exit_code == 0
    assert "Изученных категорий нет" in result.stdout


def test_show_prints_the_summary_and_switches_without_learning(monkeypatch) -> None:
    _stored_vacuum()

    async def _no_learning(*args, **kwargs):
        raise AssertionError("show must not learn")

    monkeypatch.setattr(generate_module, "generate_category_knowledge", _no_learning)

    result = _category("show", "робот-пылесос")

    assert result.exit_code == 0, result.output
    out = " ".join(result.stdout.split())
    assert "Roborock" in out and "Models: 1" in out
    assert "Как оцениваем эту категорию" in out
    assert "Состав" in out


def test_show_of_an_unlearned_category_points_to_learn_category() -> None:
    result = _category("show", "кофемашина")

    assert result.exit_code == 1
    assert "buysmarter learn-category кофемашина" in result.stdout


def test_set_and_reset_write_the_users_choice_and_say_when_it_applies() -> None:
    _stored_vacuum()

    off = _category("set", "робот-пылесос", COMPOSITION, "off")

    assert off.exit_code == 0, off.output
    assert CHANGED_NOTE in off.stdout
    assert _user("робот-пылесос", COMPOSITION) is False

    reset = _category("reset", "робот-пылесос", COMPOSITION)

    assert reset.exit_code == 0, reset.output
    assert CHANGED_NOTE in reset.stdout
    assert _user("робот-пылесос", COMPOSITION) is None


def test_a_request_level_or_unknown_capability_is_refused_with_the_allowed_ones() -> None:
    for name in ("audience", "turbo"):
        result = _category("set", "робот-пылесос", name, "on")

        assert result.exit_code == 2
        out = " ".join(result.output.split())
        assert "composition" in out and "used_market" in out
