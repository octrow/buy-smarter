"""CLI clarification: the question comes before the budget, TTY rounds, exit codes (step 7)."""

from typing import Any

import pytest
from market_sources.base import RawProduct
from tests.fakes import FakeSource
from typer.testing import CliRunner

from buysmarter.cli import main
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services import recommend as recommend_module

runner = CliRunner()

_GIFT = QueryConstraints(intent="open", options=["ирригатор", "плед"], search_phrase="подарок")


class _Stdin:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def kaspi(monkeypatch) -> FakeSource:
    """A stub Kaspi, no stored city, and the parse of each query in ``parses``."""
    source = FakeSource(
        "kaspi", [RawProduct(source="kaspi", product_id="1", title="Ирригатор", price=25000)]
    )
    original = recommend_module.recommend

    async def _recommend(query: str, **kwargs: Any):
        overrides = {"source": source, "sources": ["kaspi"], "persist": False}
        return await original(query, **{**kwargs, **overrides})

    async def _no_preference() -> str | None:
        return None

    monkeypatch.setattr(main, "run_recommend", _recommend)
    monkeypatch.setattr(recommend_module, "_city_preference", _no_preference)
    monkeypatch.setattr(main, "setup_logging", lambda *a, **kw: None)
    return source


def _tty(monkeypatch, answers: list[str]) -> list[str]:
    """A terminal answering ``answers`` in turn; returns the prompts it was shown."""
    shown: list[str] = []

    def _prompt(text: str, **kwargs: Any) -> str:
        shown.append(text)
        return answers.pop(0)

    monkeypatch.setattr(main.sys, "stdin", _Stdin(tty=True))
    monkeypatch.setattr(main.typer, "prompt", _prompt)
    return shown


def _parses(monkeypatch, by_query: dict[str, QueryConstraints]) -> list[str]:
    parsed: list[str] = []

    async def _parse(query: str, use_llm: bool) -> QueryConstraints:
        parsed.append(query)
        return by_query[query]

    monkeypatch.setattr(main, "run_parse_request", _parse)
    return parsed


async def test_the_question_comes_before_the_budget_and_a_number_picks(kaspi, monkeypatch):
    _parses(monkeypatch, {"подарок маме": _GIFT})
    shown = _tty(monkeypatch, ["1", "40000"])

    result = await main.recommend_pipeline("подарок маме", None, None, False)

    assert shown[0] == main.CLARIFY_PROMPT and shown[1] == main._BUDGET_PROMPT
    assert result.constraints.search_phrase == "ирригатор"
    assert result.constraints.budget_max == 40000
    assert kaspi.searched[0] == "ирригатор"


async def test_an_own_answer_is_added_to_the_query(kaspi, monkeypatch) -> None:
    product = QueryConstraints(search_phrase="ирригатор портативный", budget_max=40000)
    parsed = _parses(
        monkeypatch, {"подарок маме": _GIFT, "подарок маме ирригатор портативный": product}
    )
    _tty(monkeypatch, ["ирригатор портативный"])

    result = await main.recommend_pipeline("подарок маме", None, None, False)

    assert parsed == ["подарок маме", "подарок маме ирригатор портативный"]
    assert result.constraints.search_phrase == "ирригатор портативный"


async def test_the_budget_typed_once_holds_for_the_next_rounds(kaspi, monkeypatch) -> None:
    _parses(monkeypatch, {"ирригатор": QueryConstraints(search_phrase="ирригатор")})
    shown = _tty(monkeypatch, ["40000"])

    result = await main.recommend_pipeline("ирригатор", None, None, False)

    assert shown == [main._BUDGET_PROMPT]
    assert result.constraints.budget_max == 40000


def test_off_a_terminal_the_question_is_printed_with_exit_code_3(kaspi, monkeypatch) -> None:
    monkeypatch.setattr(main.sys, "stdin", _Stdin(tty=False))

    result = runner.invoke(main.app, ["recommend", "???", "--no-llm", "--no-save"])

    assert result.exit_code == 3
    assert "Не понял" in result.stdout
    assert kaspi.searched == []


def test_nothing_to_search_exits_1(kaspi, monkeypatch) -> None:
    monkeypatch.setattr(main.sys, "stdin", _Stdin(tty=False))

    result = runner.invoke(main.app, ["recommend", "???", "--no-llm", "--no-clarify", "--no-save"])

    assert result.exit_code == 1
    assert "переформулируйте" in result.stdout


def test_no_clarify_searches_the_first_option(kaspi, monkeypatch) -> None:
    _parses(monkeypatch, {"подарок маме": _GIFT})
    monkeypatch.setattr(main.sys, "stdin", _Stdin(tty=False))
    monkeypatch.setattr(main, "save_report", lambda *a, **kw: "report.md")

    result = runner.invoke(main.app, ["recommend", "подарок маме", "--no-clarify", "--no-llm"])

    assert result.exit_code == 0
    assert "Искал: ирригатор" in result.stdout
