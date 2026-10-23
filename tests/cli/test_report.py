"""Tests for the shared markdown report renderer + --save file writing."""

from buysmarter.cli.report import render_markdown, render_no_substance_markdown, save_report
from buysmarter.llm.review_summary import ReviewSummary
from buysmarter.ranking.models import (
    AxisScore,
    Candidate,
    QueryConstraints,
    Recommendation,
    ScoredCandidate,
)
from buysmarter.services.matching import UsedListing
from buysmarter.services.recommend import NoSubstanceMatch, RecommendResult


def _scored(pid: str, title: str, price: int, score: float, tier: str | None) -> ScoredCandidate:
    cand = Candidate(
        source="kaspi",
        source_product_id=pid,
        title=title,
        brand_tier=tier,
        price=price,
        url="https://kaspi.kz/p/" + pid,
    )
    return ScoredCandidate(
        candidate=cand,
        total_score=score,
        confidence=0.8,
        axes=[AxisScore(name="price", score=20.0, max_score=25.0, explanation="ok")],
        pros=["strong price"],
        cons=[],
    )


def _result(n_ranked: int = 5) -> RecommendResult:
    ranked = [
        _scored(str(i), f"Ирригатор {i}", 30000 - i * 1000, 90.0 - i, "A" if i == 0 else "C")
        for i in range(n_ranked)
    ]
    rec = Recommendation(
        best=ranked[0],
        alternatives={"best_budget": ranked[-1]},
        ranked=ranked,
        rejected_count=2,
    )
    used = [
        UsedListing(
            title="Ирригатор xiaomi me 705",
            price=12000,
            new_price=30000,
            saving=18000,
            matched_title="Xiaomi Water Flosser 2 MEO705",
            city="Алматы",
            canonical="Xiaomi Water Flosser 2 (MEO705)",
        )
    ]
    constraints = QueryConstraints(search_phrase="ирригатор", city="Almaty", budget_max=40000)
    return RecommendResult(recommendation=rec, constraints=constraints, used_listings=used)


def _top_rows(md: str) -> list[str]:
    lines = md.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("## Лучшие предложения"))
    rows = []
    for ln in lines[start + 4 :]:  # skip heading, blank, header row, separator
        if not ln.startswith("|"):
            break
        rows.append(ln)
    return rows


def test_restricted_note_is_the_first_line_under_the_title() -> None:
    result = _result().model_copy(update={"restricted_note": "Алкоголь: доставка ограничена."})

    lines = [ln for ln in render_markdown(result, top=2).splitlines() if ln]

    assert lines[1] == "> ⚠ Алкоголь: доставка ограничена."


def test_the_report_says_what_it_assumed_and_read_differently() -> None:
    base = _result()
    constraints = base.constraints.model_copy(
        update={
            "budget_min": 30000,
            "budget_note": "бюджет в другой валюте не учтён",
            "condition": "used",
        }
    )
    result = base.model_copy(update={"assumed": "крем для рук", "constraints": constraints})

    md = render_markdown(result, top=2)

    assert "Искал: крем для рук" in md
    assert "Бюджет в другой валюте не учтён." in md
    assert "Вы искали б/у" in md
    assert "**Бюджет:** от 30" in md


def test_top_limits_rows_to_requested_n() -> None:
    md = render_markdown(_result(n_ranked=5), top=2)
    assert len(_top_rows(md)) == 2
    assert "## Лучшие предложения (топ 2)" in md


def test_top_caps_at_available_when_fewer_than_requested() -> None:
    md = render_markdown(_result(n_ranked=3), top=8)
    assert len(_top_rows(md)) == 3


def test_markdown_has_all_sections() -> None:
    md = render_markdown(_result(), top=8)
    for header in (
        "# buy-smarter: рекомендация",
        "## Запрос",
        "## Лучший выбор",
        "## Другие варианты",
        "## Лучшие предложения",
        "## На OLX (частники)",
    ):
        assert header in md
    # Canonical model label surfaced in the used row.
    assert "Xiaomi Water Flosser 2 (MEO705)" in md


def test_save_writes_file_with_sections(tmp_path) -> None:
    target = tmp_path / "sub" / "report.md"
    written = save_report(_result(), "ирригатор до 40000", top=8, path=str(target))
    assert written == target
    text = target.read_text(encoding="utf-8")
    assert "## Лучший выбор" in text
    assert "## Лучшие предложения" in text


def test_alternatives_and_top_tables_link_the_product_url() -> None:
    md = render_markdown(_result(), top=8)
    assert "[Ирригатор 0](https://kaspi.kz/p/0)" in md
    assert "[Ирригатор 4](https://kaspi.kz/p/4)" in md


def test_product_without_url_renders_plain_title() -> None:
    result = _result(n_ranked=1)
    result.recommendation.best.candidate.url = None
    result.recommendation.ranked[0].candidate.url = None
    md = render_markdown(result, top=8)
    assert "| 1 | Kaspi | Ирригатор 0 |" in md


def test_used_table_links_title_no_trailing_url_list() -> None:
    """Fix C: used-listing links live inside the Listing cell, not a separate list."""
    result = _result()
    result.used_listings[0].url = "https://www.olx.kz/d/obyavlenie/xxx.html"
    md = render_markdown(result, top=8)

    assert "[Ирригатор xiaomi me 705 → Xiaomi Water Flosser 2 (MEO705)]" in md
    assert "(https://www.olx.kz/d/obyavlenie/xxx.html)" in md
    # No separate "dim" URL list line trailing the table (old bug).
    assert md.count("https://www.olx.kz/d/obyavlenie/xxx.html") == 1


def test_used_row_without_url_renders_plain_title() -> None:
    result = _result()
    result.used_listings[0].url = None
    md = render_markdown(result, top=8)

    assert "| Ирригатор xiaomi me 705 → Xiaomi Water Flosser 2 (MEO705) |" in md


def test_save_default_path_slugifies_query(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    written = save_report(_result(), "нужен ирригатор до 40000 тг", top=8, path=None)
    assert written.parent.name == "reports"
    assert written.suffix == ".md"
    assert written.exists()


def test_top_table_marks_mimic_candidate_with_warning_and_tier_cell() -> None:
    result = _result(n_ranked=1)
    result.recommendation.ranked[0].candidate.mimic_brand = "JBL"
    result.recommendation.best = result.recommendation.ranked[0]

    md = render_markdown(result, top=8)

    assert "⚠ Ирригатор 0" in md
    assert "подделка под JBL?" in md


def test_top_table_no_marker_for_clean_candidate() -> None:
    md = render_markdown(_result(n_ranked=1), top=8)
    assert "⚠" not in md


def test_rejected_reasons_breakdown_rendered() -> None:
    result = _result(n_ranked=1)
    result.recommendation.rejected_reasons = {"price exceeds budget": 3, "not available": 2}

    md = render_markdown(result, top=8)

    assert "Отсеяно:" in md
    # M5: rendered with Russian labels, ordered by count descending.
    assert "дороже бюджета ×3" in md
    assert "нет в наличии ×2" in md
    assert md.index("дороже бюджета") < md.index("нет в наличии")


def test_notable_used_listing_renders_dash_for_saving() -> None:
    result = _result(n_ranked=1)
    result.used_listings = [UsedListing(title="Waterpik WP-660 б/у", price=18000, standalone=True)]

    md = render_markdown(result, top=8)

    assert "| Waterpik WP-660 б/у | 18\xa0000 ₸ | — |" in md


def test_best_md_marks_barred_fallback_candidate() -> None:
    """M4: the markdown report must show the same "⚠" + manual-review line
    the CLI panel shows when `best` is an all-barred fallback."""
    result = _result(n_ranked=1)
    result.recommendation.best.candidate.mimic_brand = "JBL"
    result.recommendation.best.cons = ["возможная подделка под JBL"]

    md = render_markdown(result, top=8)

    assert "⚠ Ирригатор 0" in md
    assert "все кандидаты под подозрением — проверьте вручную" in md


def test_best_md_no_marker_for_clean_candidate() -> None:
    md = render_markdown(_result(n_ranked=1), top=8)
    assert "все кандидаты под подозрением" not in md


def test_best_md_shows_seller_line_when_set() -> None:
    result = _result(n_ranked=1)
    result.recommendation.best.candidate.best_seller = "Магазин X — 4.9★"

    md = render_markdown(result, top=8)

    assert "Продавец:** Магазин X — 4.9★" in md


def test_best_md_no_seller_line_by_default() -> None:
    assert "Продавец" not in render_markdown(_result(n_ranked=1), top=8)


def test_default_report_path_is_in_the_user_data_dir_not_the_working_dir(monkeypatch):
    from pathlib import Path

    from buysmarter.cli.report import default_report_path

    monkeypatch.undo()  # conftest points reports at tmp; this only computes the path
    path = default_report_path("ирригатор до 40000")
    assert path.parent == Path.home() / ".local" / "share" / "buysmarter" / "reports"
    assert path.name.endswith("-ирригатор-до-40000.md")


def test_constraints_list_required_features_and_excluded_brands() -> None:
    result = _result()
    result.constraints.required_features = ["usb-c"]
    result.constraints.excluded_brands = ["noname"]

    md = render_markdown(result)

    assert "- **Нужно:** usb-c" in md
    assert "- **Без брендов:** noname" in md


def test_best_md_shows_review_penalty_and_cons() -> None:
    result = _result()
    best = result.recommendation.best
    best.review_penalty = 7.0
    best.cons = ["шумный"]

    md = render_markdown(result)

    assert "- **Штраф:** отзывы (−7)" in md
    assert "**-** шумный" in md


def test_review_summary_section_rendered_with_verdict() -> None:
    result = _result().model_copy(
        update={
            "review_summary": ReviewSummary(
                pros=["мощный"],
                cons=["громкий"],
                common_complaints=["течёт"],
                red_flags=["подделка"],
                verdict_line="Брать можно",
            )
        }
    )

    md = render_markdown(result)

    assert "## Отзывы кратко" in md
    for line in ("- ⚠ подделка", "- ✓ мощный", "- ✗ громкий", "- «течёт»", "**Брать можно**"):
        assert line in md


def test_empty_review_summary_section_is_omitted() -> None:
    result = _result().model_copy(update={"review_summary": ReviewSummary()})

    assert "## Отзывы кратко" not in render_markdown(result)


def test_alternatives_table_lists_a_product_once_across_roles() -> None:
    result = _result()
    alt = result.recommendation.ranked[-1]
    result.recommendation.alternatives = {"best_budget": alt, "best_value": alt}

    md = render_markdown(result)

    assert "| Дешевле |" in md
    assert "| Выгоднее |" not in md


def test_no_substance_markdown_lists_alternatives_with_rating() -> None:
    rated = Candidate(source="kaspi", source_product_id="1", title="Крем А", price=5000, rating=4.5)
    unrated = Candidate(source="kaspi", source_product_id="2", title="Крем Б", price=3000)
    exc = NoSubstanceMatch(["ретинол"], checked=4, alternatives=[rated, unrated])

    md = render_no_substance_markdown(exc)

    assert "ретинол" in md
    assert "| Товар | Цена | Рейтинг |" in md
    assert "| 4.5 |" in md
    assert "| Крем Б |" in md and md.rstrip().endswith("| - |")


def test_no_substance_markdown_without_alternatives_has_no_table() -> None:
    exc = NoSubstanceMatch(["ретинол"], checked=0, alternatives=[])

    assert "| Product |" not in render_no_substance_markdown(exc)


def test_a_keyless_run_ends_the_parsed_request_with_the_skipped_steps() -> None:
    result = _result().model_copy(
        update={"skipped_llm_steps": ["query parsing", "review summaries", "ingredient extraction"]}
    )
    md = render_markdown(result, top=8)
    section = md.split("## Запрос\n", 1)[1].split("\n## ", 1)[0]
    assert section.rstrip().endswith(
        "_AI не настроен: запрос разобран по ключевым словам, без сводки отзывов и разбора "
        "состава. Ключ добавляется командой `buysmarter llm set-key <provider>`._"
    )


def test_ozon_skipped_for_lack_of_a_browser_is_noted_with_what_to_do() -> None:
    result = _result().model_copy(update={"unavailable_sources": ["ozon"]})
    md = render_markdown(result, top=8)
    section = md.split("## Запрос\n", 1)[1].split("\n## ", 1)[0]
    assert section.rstrip().endswith(
        "_Пропущено (Ozon): нет браузера. Что установить, подскажет `buysmarter doctor`._"
    )


def test_a_run_with_llm_has_no_skipped_steps_note() -> None:
    assert "AI не настроен" not in render_markdown(_result(), top=8)


def test_the_report_speaks_russian_like_the_gui() -> None:
    result = _result()
    best = result.recommendation.best
    best.candidate.brand, best.candidate.price = "Adidas", 2695
    best.axes = [
        AxisScore(name="brand", score=25.0, max_score=25.0, explanation="Adidas is a tier-A brand")
    ]
    best.pros = ["strong brand"]
    md = render_markdown(result, top=8)

    assert "| Бренд | 25.0/25 | Adidas — бренд уровня A |" in md
    assert "**+** надёжный бренд" in md
    assert "- **Цена:** 2\xa0695 ₸" in md
    assert "2,695" not in md and "strong brand" not in md


def test_titles_lose_the_category_word_kaspi_prepends() -> None:
    result = _result(n_ranked=1)
    result.recommendation.best.candidate.title = "Кроссовки Puma Кроссовки Anzarun 2 Lite"
    md = render_markdown(result, top=8)

    assert "Puma Кроссовки Anzarun 2 Lite" in md
    assert "Кроссовки Puma Кроссовки" not in md


def test_a_used_listing_of_a_tier_a_brand_asks_for_the_tag_and_receipt() -> None:
    result = _result()
    result.recommendation.ranked[0].candidate.brand = "Xiaomi"  # tier A in _result
    result.used_listings.append(UsedListing(title="Ирригатор Waterpik", price=9000))
    md = render_markdown(result, top=8)

    xiaomi_row = next(ln for ln in md.splitlines() if "xiaomi me 705" in ln)
    waterpik_row = next(ln for ln in md.splitlines() if "Waterpik" in ln)
    assert "попросите фото бирки и чек" in xiaomi_row
    assert "попросите фото бирки и чек" not in waterpik_row


def test_unchecked_composition_is_noted_with_how_many_candidates() -> None:
    result = _result(n_ranked=5)
    ranked = result.recommendation.ranked
    ranked[0].candidate.ingredients_checked = True
    result = result.model_copy(update={"ingredients_unchecked": True})

    md = render_markdown(result, top=8)

    section = md.split("## Запрос\n", 1)[1].split("\n## ", 1)[0]
    unchecked = len(ranked) - 1
    assert f"_Состав не проверен у {unchecked} из {len(ranked)} — вещество не подтверждено._" in (
        section
    )


def test_checked_composition_has_no_unchecked_note() -> None:
    assert "Состав не проверен" not in render_markdown(_result(), top=8)


def test_the_report_says_how_it_was_assessed_only_when_something_was_active() -> None:
    from buysmarter.capabilities.provenance import CapabilityState

    state = CapabilityState(
        name="composition",
        label="Состав",
        level="category",
        on=True,
        active=True,
        auto=True,
        set_by="learned",
    )
    plain = _result()

    with_state = plain.model_copy(update={"capabilities": [state]})

    assert "_Как оценивали: состав — обучение._" in render_markdown(with_state, top=2)
    assert "Как оценивали" not in render_markdown(plain, top=2)


def _with_hidden(result: RecommendResult) -> RecommendResult:
    fake = _scored("x1", "Ирригатор Waterp1k копия", 9000, 70.0, None)
    return result.model_copy(update={"hidden": [fake]})


def test_hidden_suspected_fakes_are_listed_in_the_markdown() -> None:
    md = render_markdown(_with_hidden(_result(n_ranked=2)), top=8)

    assert "Скрыто подозрительных предложений (только оригинал): 1" in md
    assert "[Ирригатор Waterp1k копия](https://kaspi.kz/p/x1)" in md


def test_no_hidden_line_without_hidden_ones() -> None:
    assert "Скрыто" not in render_markdown(_result(n_ranked=2), top=8)


def test_hidden_suspected_fakes_are_listed_in_the_console() -> None:
    from buysmarter.cli import main

    with main.console.capture() as capture:
        main._render(_with_hidden(_result(n_ranked=2)), top=3)

    out = capture.get()
    assert "Скрыто подозрительных предложений (только оригинал): 1" in out
    assert "Waterp1k" in out


def _used_query(result: RecommendResult) -> RecommendResult:
    constraints = result.constraints.model_copy(update={"condition": "used"})
    return result.model_copy(update={"constraints": constraints})


def test_a_used_query_puts_the_used_section_before_the_best_pick_and_says_so_once() -> None:
    md = render_markdown(_used_query(_result()), top=2)

    assert md.index("## На OLX (частники)") < md.index("## Лучший выбор")
    assert md.count("Вы искали б/у") == 1
    assert "смотрите раздел" not in md


def test_a_new_query_keeps_the_used_section_last() -> None:
    md = render_markdown(_result(), top=2)

    assert md.index("## Лучший выбор") < md.index("## На OLX (частники)")


def test_a_used_query_shows_the_used_table_first_in_the_console() -> None:
    from buysmarter.cli import main

    with main.console.capture() as capture:
        main._render(_used_query(_result()), top=2)

    out = capture.get()
    assert out.index("На OLX (частники)") < out.index("Best pick")
