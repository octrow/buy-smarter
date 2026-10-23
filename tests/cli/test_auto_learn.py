"""CLI auto-learning (phase 3, decision 11): the question after the report and ``--learn``."""

from typing import Any

import pytest
from typer.testing import CliRunner

from buysmarter.cli import main
from buysmarter.services.recommend import RecommendResult

runner = CliRunner()
CATEGORY = "соковыжималка"


@pytest.fixture
def cli(monkeypatch, recommend_result: RecommendResult) -> dict[str, Any]:
    """A finished run for a new category; records the condition calls, learns, and questions."""
    recommend_result.constraints.product_type = CATEGORY
    recommend_result.knowledge_missing = True
    seen: dict[str, Any] = {"condition": [], "learned": [], "asked": [], "runs": 0, "yes": False}

    async def pipeline(*_args, **_kwargs):
        seen["runs"] += 1
        return recommend_result

    async def condition(product_type, **kwargs):
        seen["condition"].append((product_type, kwargs))
        return seen.get("holds", True)

    async def learn(product_type):
        seen["learned"].append(product_type)
        return seen.get("learn_ok", True)

    def confirm(text, **_kwargs):
        seen["asked"].append(text)
        return seen["yes"]

    monkeypatch.setattr(main, "recommend_pipeline", pipeline)
    monkeypatch.setattr(main, "should_auto_learn", condition)
    monkeypatch.setattr(main, "auto_learn", learn)
    monkeypatch.setattr(main.typer, "confirm", confirm)
    monkeypatch.setattr(main, "setup_logging", lambda *a, **kw: None)
    monkeypatch.setattr(main, "_on_terminal", lambda: True)
    return seen


def _recommend(*flags: str):
    return runner.invoke(main.app, ["recommend", CATEGORY, "--no-save", *flags])


def test_on_a_terminal_the_question_follows_the_report_and_yes_learns(cli) -> None:
    cli["yes"] = True

    result = _recommend()

    assert result.exit_code == 0
    assert cli["asked"][0] == main._LEARN_PROMPT
    assert cli["learned"] == [CATEGORY]
    assert result.stdout.index("Waterpik") < result.stdout.index("изучена")


def test_the_condition_gets_the_parsed_intent_and_the_run_flags(cli) -> None:
    _recommend()

    assert cli["condition"] == [
        (CATEGORY, {"intent": "product", "use_llm": True, "knowledge_missing": True})
    ]


def test_no_means_no_learning(cli) -> None:
    result = _recommend()

    assert result.exit_code == 0
    assert cli["asked"] == [main._LEARN_PROMPT] and cli["learned"] == []


def test_after_learning_the_search_can_be_repeated(cli) -> None:
    cli["yes"] = True

    _recommend()

    assert cli["asked"] == [main._LEARN_PROMPT, main._RERUN_PROMPT]
    assert cli["runs"] == 2


def test_the_learn_flag_learns_without_the_learn_question(cli) -> None:
    result = _recommend("--learn")

    assert result.exit_code == 0
    assert cli["learned"] == [CATEGORY] and cli["asked"] == [main._RERUN_PROMPT]


def test_off_a_terminal_it_never_asks(cli, monkeypatch) -> None:
    monkeypatch.setattr(main, "_on_terminal", lambda: False)

    _recommend()

    assert cli["asked"] == [] and cli["learned"] == []


def test_the_learn_flag_works_off_a_terminal_and_does_not_ask_to_repeat(cli, monkeypatch) -> None:
    monkeypatch.setattr(main, "_on_terminal", lambda: False)

    _recommend("--learn")

    assert cli["learned"] == [CATEGORY] and cli["asked"] == [] and cli["runs"] == 1


def test_when_the_condition_fails_nothing_is_asked_or_learned(cli) -> None:
    cli["holds"] = False

    _recommend()
    _recommend("--learn")

    assert cli["asked"] == [] and cli["learned"] == []


def test_a_failed_learn_is_reported_and_the_exit_code_stays_0(cli) -> None:
    cli["yes"], cli["learn_ok"] = True, False

    result = _recommend()

    assert result.exit_code == 0
    assert "не удалось" in result.stdout
    assert cli["asked"] == [main._LEARN_PROMPT]
