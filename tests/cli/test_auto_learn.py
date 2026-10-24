"""CLI learning of a new category, the same choices as the desktop app (#26, ADR-0006).

Before the search: learn first (yes, ``--learn``) or search now while it learns in the background
(no, as «Искать без изучения»), then «Повторить поиск?». After a report without books: learn by
hand (as the report's «Изучить категорию»), then «Повторить поиск?».
"""

from typing import Any

import pytest
from typer.testing import CliRunner

from buysmarter.cli import learning, main
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services.auto_learn import NeedsLearning
from buysmarter.services.recommend import RecommendResult

runner = CliRunner()
CATEGORY = "соковыжималка"


class FakeCore:
    """A new category: ``recommend`` stops for it until a learn was tried; books after a good one.

    Records each run's ``learn_first``, the parses, the learns and the questions; ``replies``
    answers the questions in order (then ``yes``).
    """

    def __init__(self, result: RecommendResult) -> None:
        self.result = result
        self.learn_first: list[bool] = []
        self.learned: list[str] = []
        self.asked: list[tuple[str, bool | None]] = []
        self.replies: list[bool] = []
        self.yes = False
        self.new = True  # the learning condition holds
        self.learn_ok = True
        self.books = False
        self.parsed = 0

    async def parse(self, query: str, use_llm: bool) -> QueryConstraints:
        self.parsed += 1
        return QueryConstraints(search_phrase=query, budget_max=40000)  # no budget question

    async def recommend(self, query: str, **kwargs: Any) -> RecommendResult:
        self.learn_first.append(kwargs["learn_first"])
        if kwargs["learn_first"] and self.new and not self.learned:
            raise NeedsLearning(CATEGORY)
        return self.result.model_copy(update={"knowledge_missing": not self.books})

    async def learn(self, product_type: str) -> bool:
        self.learned.append(product_type)
        self.books = self.learn_ok
        return self.books

    async def learn_by_hand(self, category: str, **_: Any) -> None:
        if not await self.learn(category):
            raise RuntimeError("web search down")

    def confirm(self, text: str, **kwargs: Any) -> bool:
        self.asked.append((text, kwargs.get("default")))
        return self.replies.pop(0) if self.replies else self.yes


@pytest.fixture
def cli(monkeypatch, recommend_result: RecommendResult) -> FakeCore:
    recommend_result.constraints.product_type = CATEGORY
    core = FakeCore(recommend_result)
    monkeypatch.setattr(main, "run_parse_request", core.parse)
    monkeypatch.setattr(main, "run_recommend", core.recommend)
    monkeypatch.setattr(learning, "run_recommend", core.recommend)
    monkeypatch.setattr(learning, "auto_learn", core.learn)
    monkeypatch.setattr(learning, "learn_category", core.learn_by_hand)
    monkeypatch.setattr(main.typer, "confirm", core.confirm)
    monkeypatch.setattr(main, "setup_logging", lambda *a, **kw: None)
    monkeypatch.setattr(main, "_on_terminal", lambda: True)
    return core


def _recommend(*flags: str):
    return runner.invoke(main.app, ["recommend", CATEGORY, "--no-save", *flags])


def _first() -> tuple[str, bool]:
    return learning.LEARN_PROMPT.format(category=CATEGORY), True


def _by_hand() -> tuple[str, bool]:
    return learning.LEARN_NOW_PROMPT.format(category=CATEGORY), False


_RERUN = (learning.RERUN_PROMPT, True)


def test_yes_learns_before_the_search(cli) -> None:
    cli.yes = True

    result = _recommend()

    assert result.exit_code == 0
    assert cli.asked == [_first()]  # default yes: [Y/n]; learned, so nothing after the report
    assert cli.learned == [CATEGORY]
    assert cli.learn_first == [True, False]
    assert result.stdout.index("изучена") < result.stdout.index("Waterpik")


def test_no_searches_now_learns_in_the_background_and_offers_the_rerun(cli) -> None:
    cli.replies = [False, True]  # search now; then repeat the search

    result = _recommend()

    assert result.exit_code == 0
    assert cli.asked == [_first(), _RERUN]
    assert cli.learned == [CATEGORY]
    assert cli.learn_first == [True, False, False]  # the rerun: the settled search
    out = result.stdout
    assert out.index("Waterpik") < out.index("изучена") < out.rindex("Waterpik")


def test_no_and_no_rerun_shows_one_report(cli) -> None:
    cli.replies = [False, False]

    result = _recommend()

    assert cli.asked == [_first(), _RERUN]
    assert cli.learn_first == [True, False]
    assert result.stdout.count("Лучший выбор") == 1


def test_a_failed_background_learn_is_said_and_no_rerun_is_offered(cli) -> None:
    cli.replies, cli.learn_ok = [False], False

    result = _recommend()

    assert result.exit_code == 0
    assert "не удалось" in result.stdout
    assert cli.asked == [_first()]


def test_the_learn_flag_learns_before_the_search_without_asking(cli) -> None:
    result = _recommend("--learn")

    assert result.exit_code == 0
    assert cli.asked == [] and cli.learned == [CATEGORY]
    assert cli.learn_first == [True, False]


def test_off_a_terminal_without_the_flag_nothing_waits_or_asks(cli, monkeypatch) -> None:
    monkeypatch.setattr(main, "_on_terminal", lambda: False)

    _recommend()

    assert cli.learn_first == [False]
    assert cli.asked == [] and cli.learned == []


def test_the_learn_flag_learns_off_a_terminal(cli, monkeypatch) -> None:
    monkeypatch.setattr(main, "_on_terminal", lambda: False)

    _recommend("--learn")

    assert cli.learned == [CATEGORY] and cli.asked == []
    assert cli.learn_first == [True, False]


def test_a_failed_learn_first_is_said_and_the_search_goes_on(cli) -> None:
    cli.replies, cli.learn_ok = [True, False], False  # learn first; not by hand later

    result = _recommend()

    assert result.exit_code == 0
    assert "не удалось" in result.stdout
    assert cli.learn_first == [True, False]
    assert cli.asked == [_first(), _by_hand()]
    assert "Waterpik" in result.stdout


def test_a_report_without_books_offers_to_learn_by_hand_then_the_rerun(cli) -> None:
    cli.new = False  # the condition does not hold (limit, intent…): the report's button
    cli.yes = True

    result = _recommend()

    assert cli.asked == [_by_hand(), _RERUN]
    assert cli.learned == [CATEGORY]
    assert cli.learn_first == [True, False]
    assert result.stdout.count("Лучший выбор") == 2


def test_learning_by_hand_that_fails_is_said(cli) -> None:
    cli.new, cli.learn_ok, cli.yes = False, False, True

    result = _recommend()

    assert result.exit_code == 0
    assert "не удалось" in result.stdout
    assert cli.asked == [_by_hand()]


def test_no_learning_by_hand_off_a_terminal_or_without_the_llm(cli, monkeypatch) -> None:
    cli.new = False
    _recommend("--no-llm")
    monkeypatch.setattr(main, "_on_terminal", lambda: False)
    _recommend()

    assert cli.asked == []


def test_a_used_up_hourly_limit_is_said_after_the_report(cli, monkeypatch) -> None:
    cli.new = False
    monkeypatch.setattr(main, "over_limit", lambda: True)

    result = _recommend()

    assert "лимит" in result.stdout


def test_a_zero_hourly_limit_says_learning_is_off(cli, monkeypatch) -> None:
    cli.new = False
    monkeypatch.setattr(main, "over_limit", lambda: True)
    monkeypatch.setenv("BUYSMARTER_AUTO_LEARN_PER_HOUR", "0")

    result = _recommend()

    assert "выключено" in result.stdout


def test_the_rerun_repeats_the_settled_search_without_asking_again(cli) -> None:
    # as the app's «Повторить поиск»: the same answer, round and budget, no new parse
    cli.replies = [False, True]

    _recommend()

    assert cli.parsed == 1
    assert cli.asked == [_first(), _RERUN]
