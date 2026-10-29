"""MCP server tests: tool registry, recommend markdown, preferences.

No network/LLM — the service layer is stubbed; preferences use the suite's tmp store.
"""

import asyncio
import inspect
import logging

import pytest

from buysmarter.cli import report
from buysmarter.mcp_server import server
from buysmarter.ranking.models import (
    AxisScore,
    Candidate,
    QueryConstraints,
    Recommendation,
    ScoredCandidate,
)
from buysmarter.services.matching import UsedListing
from buysmarter.services.recommend import NoNewPick, RecommendResult
from buysmarter.services.understanding import Clarification, NeedsClarification

_EXPECTED_TOOLS = {
    "recommend",
    "search_products",
    "compare",
    "get_preferences",
    "set_preference",
}


async def _get_tool(name: str):
    return await server.mcp.get_tool(name)


# --- registry -------------------------------------------------------------


async def test_all_tools_registered_with_expected_names() -> None:
    for name in _EXPECTED_TOOLS:
        tool = await _get_tool(name)
        assert tool is not None, f"tool {name!r} not registered"


async def test_recommend_signature_matches_contract() -> None:
    tool = await _get_tool("recommend")
    params = inspect.signature(tool.fn).parameters
    assert set(params) == {
        "query",
        "city",
        "budget",
        "sources",
        "trusted_only",
        "top",
        "parse_with_llm",
        "answer",
        "clarify_round",
        "no_clarify",
    }
    # LLM parsing must default OFF (the MCP client is the conversational brain).
    assert params["parse_with_llm"].default is False


async def test_tools_have_docstring_descriptions() -> None:
    for name in _EXPECTED_TOOLS:
        tool = await _get_tool(name)
        assert tool.description and len(tool.description) > 20


# --- recommend returns markdown -------------------------------------------


def _stub_result() -> RecommendResult:
    cand = Candidate(
        source="kaspi",
        source_product_id="k1",
        title="Waterpik WP-100 ирригатор",
        brand="Waterpik",
        price=30000,
    )
    scored = ScoredCandidate(
        candidate=cand,
        total_score=88.0,
        confidence=0.9,
        axes=[AxisScore(name="brand", score=25.0, max_score=25, explanation="tier A")],
        pros=["надёжный бренд"],
        cons=[],
    )
    rec = Recommendation(
        best=scored,
        alternatives={},
        ranked=[scored],
        rejected_count=0,
    )
    constraints = QueryConstraints(search_phrase="ирригатор", city="Алматы", budget_max=40000)
    return RecommendResult(recommendation=rec, constraints=constraints, used_listings=[])


async def test_recommend_tool_returns_markdown_with_sections(monkeypatch) -> None:
    async def fake_recommend(query, **kwargs):
        assert kwargs["use_llm"] is False  # naive parser by default
        assert kwargs["city"] == "Алматы"
        return _stub_result()

    monkeypatch.setattr(server, "run_recommend", fake_recommend)

    tool = await _get_tool("recommend")
    out = await tool.fn("нужен ирригатор", city="Алматы")

    assert "# buy-smarter: рекомендация" in out
    assert "## Лучший выбор" in out
    assert "Waterpik WP-100" in out


async def test_recommend_tool_leaves_the_city_to_the_query_when_none_is_given(monkeypatch) -> None:
    seen = {}

    async def fake_recommend(query, **kwargs):
        seen["city"] = kwargs["city"]
        return _stub_result()

    monkeypatch.setattr(server, "run_recommend", fake_recommend)

    tool = await _get_tool("recommend")
    await tool.fn("ирригатор в Шымкенте")

    assert seen["city"] is None  # recommend() reads the query's city, then the saved one


async def test_recommend_tool_saves_the_report_to_past_searches(monkeypatch) -> None:
    async def fake_recommend(query, **kwargs):
        return _stub_result()

    monkeypatch.setattr(server, "run_recommend", fake_recommend)

    tool = await _get_tool("recommend")
    await tool.fn("нужен ирригатор", city="Алматы")

    [path] = report.saved_reports()
    assert report.report_parts(path)[1] == "нужен ирригатор"


async def test_recommend_tool_asks_the_host_to_ask_the_human(monkeypatch) -> None:
    asked = Clarification(
        kind="multi", question="С чего начнём?", options=["iPhone 15", "S24"], round=1
    )
    seen: dict = {}

    async def ask(query, **kwargs):
        seen.update(kwargs)
        raise NeedsClarification(asked)

    monkeypatch.setattr(server, "run_recommend", ask)

    tool = await _get_tool("recommend")
    out = await tool.fn("iPhone 15 или S24", city="Алматы", answer="iPhone", clarify_round=1)

    assert "С чего начнём?" in out and "1. iPhone 15" in out
    assert "answer" in out and "clarify_round=2" in out
    assert (seen["answer"], seen["clarify_round"], seen["no_clarify"]) == ("iPhone", 1, False)


async def test_recommend_tool_reports_value_error_gracefully(monkeypatch) -> None:
    async def boom(query, **kwargs):
        raise ValueError("no candidates survived hard filters")

    monkeypatch.setattr(server, "run_recommend", boom)

    tool = await _get_tool("recommend")
    out = await tool.fn("ерунда", city="Алматы")
    assert out.startswith("No recommendation:")


async def test_recommend_tool_shows_the_olx_ads_when_nothing_new_passes(monkeypatch) -> None:
    ad = UsedListing(title="iPhone 13 128GB", price=180_000, url="https://olx.kz/1")

    async def nothing_new(query, **kwargs):
        raise NoNewPick({"price exceeds budget": 2}, [ad])

    monkeypatch.setattr(server, "run_recommend", nothing_new)

    tool = await _get_tool("recommend")
    out = await tool.fn("iphone 13 до 250000", city="Алматы")

    assert "Новых предложений нет: дороже бюджета ×2. На OLX нашлось:" in out
    assert "[iPhone 13 128GB](https://olx.kz/1)" in out


async def test_recommend_tool_says_olx_is_empty_too(monkeypatch) -> None:
    async def nothing_at_all(query, **kwargs):
        raise NoNewPick({"price exceeds budget": 2}, [])

    monkeypatch.setattr(server, "run_recommend", nothing_at_all)

    tool = await _get_tool("recommend")
    out = await tool.fn("iphone 13 до 250000", city="Алматы")

    assert "На OLX тоже ничего." in out
    assert "| Объявление |" not in out


# --- preferences (the suite's tmp store) -----------------------------------


async def test_preferences_roundtrip_through_the_store() -> None:
    set_tool = await _get_tool("set_preference")
    get_tool = await _get_tool("get_preferences")

    confirm = await set_tool.fn("city", "Астана")
    out = await get_tool.fn()

    assert "city" in confirm
    assert "city" in out and "Астана" in out


async def test_get_preferences_empty_message() -> None:
    tool = await _get_tool("get_preferences")
    out = await tool.fn()
    assert "No preferences stored" in out


async def test_recommend_rejects_unknown_sources(monkeypatch) -> None:
    async def boom(*a, **k):
        raise AssertionError("pipeline must not run")

    monkeypatch.setattr(server, "run_recommend", boom)
    out = await server.recommend("q", city="Алматы", sources="amazon")
    assert "amazon" in out


async def test_search_products_normalizes_source_name(monkeypatch) -> None:
    seen = {}

    class Src:
        async def search(self, query, city=None):
            seen["city"] = city
            return []

    monkeypatch.setattr(server, "_source_for", lambda name: seen.setdefault("name", name) and Src())
    await server.search_products("q", source=" Kaspi ", city="Астана")
    assert seen["name"] == "kaspi"
    assert seen["city"] == "710000000"


async def test_search_products_passes_the_olx_city_id(monkeypatch) -> None:
    seen = {}

    class Src:
        async def search(self, query, city=None):
            seen["city"] = city
            return []

    monkeypatch.setattr(server, "_source_for", lambda name: Src())
    await server.search_products("q", source="olx", city="Шымкент")
    assert seen["city"] == "47"


async def test_search_products_nonpositive_limit_is_clamped(monkeypatch) -> None:
    from market_sources.base import RawProduct

    class Src:
        async def search(self, query, city=None):
            return [RawProduct(source="kaspi", product_id=str(i), title=f"p{i}") for i in range(3)]

    monkeypatch.setattr(server, "_source_for", lambda name: Src())
    out = await server.search_products("q", limit=-1)
    assert out.count("| p") == 1


async def test_recommend_nonpositive_top_is_clamped(monkeypatch) -> None:
    seen = {}

    async def fake(*a, **k):
        return _stub_result()

    monkeypatch.setattr(server, "run_recommend", fake)
    monkeypatch.setattr(
        server, "render_markdown", lambda result, top, **_: str(seen.setdefault("top", top))
    )
    await server.recommend("q", city="Алматы", top=-3)
    assert seen["top"] == 1


def test_tables_escape_pipes() -> None:
    from market_sources.base import RawProduct

    p = RawProduct(source="kaspi", product_id="1", title="a|b")
    assert "a\\|b" in server._products_table([p], 5)
    out = server._compare_table({"x|y": {"k": "v|w"}})
    assert "x\\|y" in out
    assert "v\\|w" in out


async def test_compare_survives_specs_failure(monkeypatch) -> None:
    from market_sources.base import RawProduct

    class Src:
        async def search(self, name):
            return [RawProduct(source="kaspi", product_id=name, title=name)]

        async def specs(self, pid):
            if pid == "bad":
                raise RuntimeError("boom")
            return {"Color": "red"}

    monkeypatch.setattr(server, "_source_for", lambda name: Src())
    out = await server.compare("good, bad")
    assert "red" in out
    assert "bad" in out
    assert "error" in out.lower()


def test_ozon_without_a_browser_is_a_clear_error():
    import pytest

    with pytest.raises(ValueError, match="no browser"):
        server._source_for("ozon")


# --- auto-learning in the background (phase 3, decision 11) ----------------


def _missing_result() -> RecommendResult:
    result = _stub_result()
    result.constraints.product_type = "соковыжималка"
    result.knowledge_missing = True
    return result


@pytest.fixture
def learn_calls(monkeypatch) -> list[str]:
    calls: list[str] = []

    async def fake_recommend(query, **kwargs):
        return _missing_result()

    async def condition(*_a, **_k):
        return True

    async def fake_learn(product_type: str) -> bool:
        calls.append(product_type)
        return True

    monkeypatch.setattr(server, "run_recommend", fake_recommend)
    monkeypatch.setattr(server, "should_auto_learn", condition)
    monkeypatch.setattr(server, "auto_learn", fake_learn)
    return calls


async def test_a_new_category_is_learned_in_the_background(learn_calls) -> None:
    tool = await _get_tool("recommend")
    out = await tool.fn("соковыжималка", parse_with_llm=True)
    await asyncio.gather(*server._LEARNING)

    assert learn_calls == ["соковыжималка"]
    assert "изучается" in out and "повторите запрос" in out


async def test_the_condition_gets_the_request_llm_flag(monkeypatch, learn_calls) -> None:
    seen: dict = {}

    async def condition(product_type, **kwargs):
        seen.update(kwargs, product_type=product_type)
        return False

    monkeypatch.setattr(server, "should_auto_learn", condition)
    tool = await _get_tool("recommend")
    out = await tool.fn("соковыжималка")

    assert seen == {
        "product_type": "соковыжималка",
        "intent": "product",
        "use_llm": False,
        "knowledge_missing": True,
    }
    assert learn_calls == [] and "изучается" not in out


async def test_a_learn_that_raises_is_logged(monkeypatch, learn_calls, caplog) -> None:
    async def boom(product_type: str) -> bool:
        raise RuntimeError("marks store down")

    monkeypatch.setattr(server, "auto_learn", boom)
    tool = await _get_tool("recommend")
    with caplog.at_level(logging.ERROR, logger=server.logger.name):
        await tool.fn("соковыжималка", parse_with_llm=True)
        await asyncio.gather(*server._LEARNING, return_exceptions=True)
        await asyncio.sleep(0)

    assert any(r.exc_info and "соковыжималка" in r.getMessage() for r in caplog.records)
