"""CLI learning of a new category before its search (#26): the question and ``--learn``."""

from typing import Any

import pytest
from typer.testing import CliRunner

from buysmarter.cli import main
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services.auto_learn import NeedsLearning
from buysmarter.services.recommend import RecommendResult

runner = CliRunner()
CATEGORY = "соковыжималка"


@pytest.fixture
def cli(monkeypatch, recommend_result: RecommendResult) -> dict[str, Any]:
    """A new category: ``recommend`` stops for it while ``learn_first`` is on.

    Records each run's ``learn_first``, the learns and the questions.
    """
    recommend_result.knowledge_missing = True
    seen: dict[str, Any] = {"learn_first": [], "learned": [], "asked": [], "yes": False}

    async def parse(query: str, use_llm: bool) -> QueryConstraints:
        return QueryConstraints(search_phrase=query, budget_max=40000)  # no budget question

    async def recommend(query: str, **kwargs: Any) -> RecommendResult:
        seen["learn_first"].append(kwargs["learn_first"])
        if kwargs["learn_first"] and seen.get("new", True):
            raise NeedsLearning(CATEGORY)
        return recommend_result

    async def learn(product_type: str) -> bool:
        seen["learned"].append(product_type)
        return seen.get("learn_ok", True)

    def confirm(text: str, **kwargs: Any) -> bool:
        seen["asked"].append((text, kwargs.get("default")))
        return seen["yes"]

    monkeypatch.setattr(main, "run_parse_request", parse)
    monkeypatch.setattr(main, "run_recommend", recommend)
    monkeypatch.setattr(main, "auto_learn", learn)
    monkeypatch.setattr(main.typer, "confirm", confirm)
    monkeypatch.setattr(main, "setup_logging", lambda *a, **kw: None)
    monkeypatch.setattr(main, "_on_terminal", lambda: True)
    return seen


def _recommend(*flags: str):
    return runner.invoke(main.app, ["recommend", CATEGORY, "--no-save", *flags])


def _question() -> tuple[str, bool]:
    return main._LEARN_PROMPT.format(category=CATEGORY), True


def test_on_a_terminal_the_question_comes_before_the_search_and_yes_learns(cli) -> None:
    cli["yes"] = True

    result = _recommend()

    assert result.exit_code == 0
    assert cli["asked"] == [_question()]  # default yes: [Y/n]
    assert cli["learned"] == [CATEGORY]
    assert cli["learn_first"] == [True, False]
    assert result.stdout.index("изучена") < result.stdout.index("Waterpik")


def test_no_searches_without_learning(cli) -> None:
    result = _recommend()

    assert result.exit_code == 0
    assert cli["learned"] == [] and cli["learn_first"] == [True, False]
    assert "Waterpik" in result.stdout


def test_the_learn_flag_learns_before_the_search_without_asking(cli) -> None:
    result = _recommend("--learn")

    assert result.exit_code == 0
    assert cli["asked"] == [] and cli["learned"] == [CATEGORY]
    assert cli["learn_first"] == [True, False]


def test_off_a_terminal_without_the_flag_nothing_waits(cli, monkeypatch) -> None:
    monkeypatch.setattr(main, "_on_terminal", lambda: False)

    _recommend()

    assert cli["learn_first"] == [False]
    assert cli["asked"] == [] and cli["learned"] == []


def test_the_learn_flag_learns_off_a_terminal(cli, monkeypatch) -> None:
    monkeypatch.setattr(main, "_on_terminal", lambda: False)

    _recommend("--learn")

    assert cli["learned"] == [CATEGORY] and cli["asked"] == []
    assert cli["learn_first"] == [True, False]


def test_a_category_that_may_not_learn_is_searched_at_once(cli) -> None:
    cli["new"] = False

    _recommend()

    assert cli["learn_first"] == [True]
    assert cli["asked"] == [] and cli["learned"] == []


def test_a_failed_learn_is_reported_and_the_search_goes_on(cli) -> None:
    cli["yes"], cli["learn_ok"] = True, False

    result = _recommend()

    assert result.exit_code == 0
    assert "не удалось" in result.stdout
    assert cli["learn_first"] == [True, False]
    assert "Waterpik" in result.stdout


def test_a_used_up_hourly_limit_is_said_after_the_report(cli, monkeypatch) -> None:
    cli["new"] = False
    monkeypatch.setattr(main, "over_limit", lambda: True)

    result = _recommend()

    assert "лимит" in result.stdout


def test_a_zero_hourly_limit_says_learning_is_off(cli, monkeypatch) -> None:
    cli["new"] = False
    monkeypatch.setattr(main, "over_limit", lambda: True)
    monkeypatch.setenv("BUYSMARTER_AUTO_LEARN_PER_HOUR", "0")

    result = _recommend()

    assert "выключено" in result.stdout
