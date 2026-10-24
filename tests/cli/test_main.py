"""CLI-level tests: city defaulting panel note + budget prompt gating.

No network/DB/LLM: ``recommend_pipeline`` is exercised with ``use_llm=False``
and a stubbed Kaspi source so parsing stays deterministic.
"""

import pytest
from market_sources.base import RawProduct
from typer.testing import CliRunner

from buysmarter.cli import console as terminal
from buysmarter.cli import main
from buysmarter.llm.review_summary import ReviewSummary
from buysmarter.services.matching import UsedListing
from buysmarter.services.recommend import NoNewPick

runner = CliRunner()


def test_review_summary_panel_rendered() -> None:
    summary = ReviewSummary(
        pros=["мощный напор"],
        cons=["шумный"],
        common_complaints=["протекает со временем"],
        verdict_line="Хороший выбор за свои деньги",
    )
    with main.console.capture() as capture:
        terminal._render_review_summary(summary)
    out = capture.get()

    assert "Отзывы кратко" in out
    assert "мощный напор" in out
    assert "шумный" in out
    assert "протекает" in out
    assert "Хороший выбор" in out


def test_review_summary_panel_skipped_when_none() -> None:
    with main.console.capture() as capture:
        terminal._render_review_summary(None)
    assert capture.get() == ""


class StubKaspiSource:
    """Minimal ``ProductSource`` double: one product, empty specs/offers."""

    name = "kaspi"

    def __init__(self) -> None:
        self._products = [
            RawProduct(source="kaspi", product_id="1", title="Ирригатор Waterpik", price=25000)
        ]

    async def search(self, query: str, city: str | None = None) -> list[RawProduct]:
        return self._products

    async def specs(self, product_id: str) -> dict[str, str]:
        return {}

    async def offers(self, product_id: str):
        return []

    async def reviews(self, product_id: str):  # pragma: no cover - unused here
        return []


@pytest.fixture
def stub_source() -> StubKaspiSource:
    return StubKaspiSource()


async def test_city_default_note_rendered_when_unstated(
    stub_source: StubKaspiSource, monkeypatch
) -> None:
    from buysmarter.services import recommend as recommend_module

    async def _no_preference() -> str | None:
        return None

    monkeypatch.setattr(recommend_module, "_city_preference", _no_preference)
    result = await recommend_module.recommend(
        "ирригатор портативный до 40000 тг",
        use_llm=False,
        source=stub_source,
        persist=False,
    )

    assert result.city_defaulted is True
    assert result.constraints.city == "Алматы"


async def test_restricted_goods_are_searched_with_a_warning_first(
    stub_source: StubKaspiSource, monkeypatch
) -> None:
    """Owner's decision: a vape is searched, not refused; the warning leads the report."""
    from buysmarter.services import recommend as recommend_module

    async def _no_preference() -> str | None:
        return None

    monkeypatch.setattr(recommend_module, "_city_preference", _no_preference)
    result = await recommend_module.recommend(
        "вейп до 30000 тг", use_llm=False, source=stub_source, persist=False
    )

    assert result.constraints.restricted == "tobacco"
    assert result.restricted_note
    assert result.recommendation.best is not None
    with main.console.capture() as capture:
        terminal.render(result, top=3)
    first_line = capture.get().strip().splitlines()[0]
    assert first_line.startswith("⚠ Табак")


class _FakeStdin:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_budget_prompt_skipped_when_not_a_tty(monkeypatch) -> None:
    prompted = False

    def _fake_prompt(*args, **kwargs):
        nonlocal prompted
        prompted = True
        return "50000"

    monkeypatch.setattr(main.sys, "stdin", _FakeStdin(tty=False))
    monkeypatch.setattr(main.typer, "prompt", _fake_prompt)

    resolved = main._resolve_budget(None, None)

    assert resolved is None
    assert prompted is False


def test_budget_prompt_used_on_tty_when_no_budget_anywhere(monkeypatch) -> None:
    monkeypatch.setattr(main.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(main.typer, "prompt", lambda *a, **kw: "50000")

    resolved = main._resolve_budget(None, None)

    assert resolved == 50000


def test_budget_prompt_empty_answer_means_no_limit(monkeypatch) -> None:
    monkeypatch.setattr(main.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(main.typer, "prompt", lambda *a, **kw: "")

    resolved = main._resolve_budget(None, None)

    assert resolved is None


def test_budget_flag_wins_without_prompting(monkeypatch) -> None:
    def _fail_prompt(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("should not prompt when --budget is given")

    monkeypatch.setattr(main.typer, "prompt", _fail_prompt)

    resolved = main._resolve_budget(15000, None)

    assert resolved == 15000


def test_budget_in_query_skips_prompt(monkeypatch) -> None:
    def _fail_prompt(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("should not prompt when the query already states a budget")

    monkeypatch.setattr(main.typer, "prompt", _fail_prompt)

    resolved = main._resolve_budget(None, 40000)

    assert resolved is None


def test_a_saved_budget_skips_the_prompt(monkeypatch) -> None:
    # as the app: the saved budget is the fallback, nothing to ask
    def _fail_prompt(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("should not prompt when a budget is saved")

    monkeypatch.setattr(main.sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr(main.typer, "prompt", _fail_prompt)

    resolved = main._resolve_budget(None, None, saved=40000)

    assert resolved is None  # the core falls back to the saved one


def _stub_recommend_pipeline(monkeypatch, result) -> None:
    async def _fake_pipeline(*args, **kwargs):
        return result

    monkeypatch.setattr(main, "recommend_pipeline", _fake_pipeline)
    monkeypatch.setattr(main, "render", lambda *a, **kw: None)
    # The Typer callback wires up real logging on every invocation; keep these
    # unit tests from touching the real log file.
    monkeypatch.setattr(main, "setup_logging", lambda *a, **kw: None)


def test_recommend_saves_report_by_default(monkeypatch, tmp_path, recommend_result) -> None:
    """Reports must be written automatically — no --save flag needed."""
    saved_paths: list = []
    monkeypatch.setattr(
        main, "save_report", lambda *a, **kw: saved_paths.append(1) or tmp_path / "r.md"
    )
    _stub_recommend_pipeline(monkeypatch, recommend_result)

    result = runner.invoke(main.app, ["recommend", "ирригатор до 40000"])

    assert result.exit_code == 0
    assert saved_paths == [1]
    assert "Report saved to" in result.stdout


def test_recommend_no_save_skips_report(monkeypatch, tmp_path, recommend_result) -> None:
    saved_paths: list = []
    monkeypatch.setattr(
        main, "save_report", lambda *a, **kw: saved_paths.append(1) or tmp_path / "r.md"
    )
    _stub_recommend_pipeline(monkeypatch, recommend_result)

    result = runner.invoke(main.app, ["recommend", "ирригатор до 40000", "--no-save"])

    assert result.exit_code == 0
    assert saved_paths == []
    assert "Report saved to" not in result.stdout


def test_recommend_save_path_saves_even_with_no_save(
    monkeypatch, tmp_path, recommend_result
) -> None:
    saved_paths: list = []
    target = tmp_path / "custom.md"
    monkeypatch.setattr(main, "save_report", lambda *a, **kw: saved_paths.append(1) or target)
    _stub_recommend_pipeline(monkeypatch, recommend_result)

    result = runner.invoke(
        main.app,
        ["recommend", "ирригатор до 40000", "--no-save", "--save-path", str(target)],
    )

    assert result.exit_code == 0
    assert saved_paths == [1]


def _scored_candidate(pid: str, mimic_brand: str | None = None, dup_price_gap=None):
    from buysmarter.ranking.models import AxisScore, Candidate, ScoredCandidate

    cand = Candidate(
        source="kaspi",
        source_product_id=pid,
        title=f"Product {pid}",
        price=10000,
        mimic_brand=mimic_brand,
        dup_price_gap=dup_price_gap,
    )
    cons = [] if mimic_brand is None else [f"возможная подделка под {mimic_brand}"]
    return ScoredCandidate(
        candidate=cand,
        total_score=80.0,
        confidence=0.8,
        axes=[AxisScore(name="price", score=20.0, max_score=25.0, explanation="ok")],
        cons=cons,
    )


def test_render_top_marks_barred_candidate_with_warning() -> None:
    clean = _scored_candidate("1")
    mimic = _scored_candidate("2", mimic_brand="JBL")

    with main.console.capture() as capture:
        terminal._render_top([clean, mimic], top=8)
    out = capture.get()

    assert "⚠ Product 2" in out
    assert "Product 1" in out and "⚠ Product 1" not in out
    assert "подделка под JBL?" in out


def test_render_used_shows_dash_for_standalone_saving() -> None:
    from buysmarter.services.matching import UsedListing

    listing = UsedListing(title="Waterpik WP-660 б/у", price=18000, standalone=True)

    with main.console.capture() as capture:
        terminal.render_used([listing], [])
    out = capture.get()

    assert "—" in out
    assert "-18,000" not in out


def test_render_best_marks_barred_fallback_candidate() -> None:
    """M4: `best` can only be barred via the all-barred assemble_recommendation
    fallback — must show the "⚠" prefix and the manual-review line."""
    mimic_best = _scored_candidate("1", mimic_brand="JBL")

    with main.console.capture() as capture:
        terminal._render_best(mimic_best)
    out = capture.get()

    assert "⚠ Product 1" in out
    assert "все кандидаты под подозрением — проверьте вручную" in out


def test_render_best_no_marker_for_clean_candidate() -> None:
    clean_best = _scored_candidate("1")

    with main.console.capture() as capture:
        terminal._render_best(clean_best)
    out = capture.get()

    assert "⚠" not in out
    assert "все кандидаты под подозрением" not in out


def test_render_top_survives_markup_like_title() -> None:
    from buysmarter.ranking.models import Candidate, ScoredCandidate

    cand = Candidate(
        source="olx", source_product_id="9", title="Phone [/x] [IPX7]", price=1000, url="u"
    )
    scored = ScoredCandidate(
        candidate=cand, total_score=50.0, confidence=0.5, axes=[], pros=[], cons=[]
    )
    with main.console.capture() as capture:
        terminal._render_top([scored], 5)
    assert "[IPX7]" in capture.get()


def test_render_best_survives_markup_like_text() -> None:
    from buysmarter.ranking.models import AxisScore, Candidate, ScoredCandidate

    cand = Candidate(source="olx", source_product_id="9", title="A [/x]", price=1000)
    scored = ScoredCandidate(
        candidate=cand,
        total_score=50.0,
        confidence=0.5,
        axes=[AxisScore(name="p", score=1.0, max_score=2.0, explanation="see [/y]")],
        pros=["good [/z]"],
        cons=["bad [/w]"],
    )
    with main.console.capture() as capture:
        terminal._render_best(scored)
    assert "[/x]" in capture.get()


@pytest.mark.parametrize("top", ["0", "-1"])
def test_recommend_rejects_nonpositive_top(top: str) -> None:
    result = runner.invoke(main.app, ["recommend", "ирригатор", "--top", top, "--no-save"])
    assert result.exit_code == 2


def _fake_ozon_browser(monkeypatch, passes: bool) -> list[dict]:
    from buysmarter.services import doctor, ozon_browser

    made: list[dict] = []

    class FakeOzonBrowser:
        def __init__(self, **kwargs):
            made.append(kwargs)

        def composer_url(self, path):
            return f"https://www.ozon.kz/api/composer-api.bx/page/json/v2?url={path}"

        async def bootstrap(self, probe_url):
            assert "search" in probe_url
            return passes

    monkeypatch.setattr(ozon_browser, "launches", lambda browser: browser == "chromium")
    monkeypatch.setattr(doctor, "OzonBrowser", FakeOzonBrowser)
    return made


def test_ozon_setup_reports_success_and_saves_to_the_default_path(monkeypatch, tmp_path) -> None:
    state = tmp_path / "ozon.json"
    monkeypatch.setenv("OZON_STATE_PATH", str(state))
    made = _fake_ozon_browser(monkeypatch, passes=True)
    result = runner.invoke(main.app, ["ozon-setup"])
    assert result.exit_code == 0
    assert "сохранена" in result.output
    assert [(kw["state_path"], kw["channel"]) for kw in made] == [(state, None)]


def test_ozon_setup_exits_nonzero_on_timeout(monkeypatch) -> None:
    _fake_ozon_browser(monkeypatch, passes=False)
    result = runner.invoke(main.app, ["ozon-setup"])
    assert result.exit_code == 1
    assert "Не удалось" in result.output


def _raise_from_pipeline(monkeypatch, exc: Exception) -> None:
    async def _fake_pipeline(*args, **kwargs):
        raise exc

    monkeypatch.setattr(main, "recommend_pipeline", _fake_pipeline)
    monkeypatch.setattr(main, "setup_logging", lambda *a, **kw: None)


def test_nothing_new_in_budget_prints_the_olx_ads_and_succeeds(monkeypatch) -> None:
    ad = UsedListing(title="iPhone 13 128GB", price=180_000, new_price=373_000, saving=193_000)
    _raise_from_pipeline(monkeypatch, NoNewPick({"price exceeds budget": 2}, [ad]))

    result = runner.invoke(main.app, ["recommend", "iphone 13 б/у до 250000", "--no-save"])

    assert result.exit_code == 0
    assert "Новых предложений нет: дороже бюджета ×2. На OLX нашлось:" in result.stdout
    assert "iPhone 13 128GB" in result.stdout


def test_nothing_new_and_nothing_on_olx_is_a_failed_search(monkeypatch) -> None:
    _raise_from_pipeline(monkeypatch, NoNewPick({"price exceeds budget": 2}, []))

    result = runner.invoke(main.app, ["recommend", "iphone 13 б/у до 250000", "--no-save"])

    assert result.exit_code == 1
    assert "На OLX тоже ничего." in result.stdout


def test_the_console_report_says_how_it_was_assessed(recommend_result) -> None:
    from buysmarter.capabilities.provenance import CapabilityState

    state = CapabilityState(
        name="composition",
        label="Состав",
        level="category",
        on=False,
        active=False,
        auto=True,
        set_by="learned",
        user=False,
    )
    result = recommend_result.model_copy(update={"capabilities": [state]})

    with main.console.capture() as capture:
        terminal.render(result, 3)

    assert "Как оценивали: состав — выключено вами." in capture.get()
