"""The CLI searches with the saved marketplaces and excluded brands, as the app (parity #8)."""

from typing import Any

import pytest
from typer.testing import CliRunner

from buysmarter.cli import main
from buysmarter.memory import prefs
from buysmarter.ranking.models import QueryConstraints
from buysmarter.services.collect import ALL_SOURCES
from buysmarter.services.recommend import RecommendResult

runner = CliRunner()


@pytest.fixture
def calls(monkeypatch, recommend_result: RecommendResult) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def parse(query: str, use_llm: bool) -> QueryConstraints:
        return QueryConstraints(search_phrase=query, budget_max=40000, excluded_brands=["Xiaomi"])

    async def recommend(query: str, **kwargs: Any) -> RecommendResult:
        seen.append(kwargs)
        return recommend_result

    monkeypatch.setattr(main, "run_parse_request", parse)
    monkeypatch.setattr(main, "run_recommend", recommend)
    monkeypatch.setattr(main, "setup_logging", lambda *a, **kw: None)
    return seen


def _saved(monkeypatch, **values: Any) -> None:
    async def get_all() -> dict[str, Any]:
        return values

    monkeypatch.setattr(prefs, "get_all", get_all)


def _recommend(*flags: str):
    return runner.invoke(main.app, ["recommend", "ирригатор", "--no-save", *flags])


def test_without_the_flag_the_saved_marketplaces_are_searched(calls, monkeypatch) -> None:
    _saved(monkeypatch, marketplaces=["kaspi", "olx"])

    assert _recommend().exit_code == 0
    assert calls[0]["sources"] == ["kaspi", "olx"]


def test_the_flag_beats_the_saved_marketplaces(calls, monkeypatch) -> None:
    _saved(monkeypatch, marketplaces=["kaspi"])

    _recommend("--sources", "olx")

    assert calls[0]["sources"] == ["olx"]


def test_with_nothing_saved_every_marketplace_is_searched(calls, monkeypatch) -> None:
    _saved(monkeypatch)

    _recommend()

    assert calls[0]["sources"] == list(ALL_SOURCES)


def test_the_saved_excluded_brands_join_the_query_s_own(calls, monkeypatch) -> None:
    _saved(monkeypatch, excluded_brands="Noname, Xiaomi")

    _recommend()

    assert calls[0]["parsed"].excluded_brands == ["Xiaomi", "Noname"]
