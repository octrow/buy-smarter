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
    constraints = QueryConstraints(category="ирригатор", city="Almaty", budget_max=40000)
    return RecommendResult(recommendation=rec, constraints=constraints, used_listings=used)


def _top_rows(md: str) -> list[str]:
    lines = md.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("## Top candidates"))
    rows = []
    for ln in lines[start + 4 :]:  # skip heading, blank, header row, separator
        if not ln.startswith("|"):
            break
        rows.append(ln)
    return rows


def test_top_limits_rows_to_requested_n() -> None:
    md = render_markdown(_result(n_ranked=5), top=2)
    assert len(_top_rows(md)) == 2
    assert "## Top candidates (top 2)" in md


def test_top_caps_at_available_when_fewer_than_requested() -> None:
    md = render_markdown(_result(n_ranked=3), top=8)
    assert len(_top_rows(md)) == 3


def test_markdown_has_all_sections() -> None:
    md = render_markdown(_result(), top=8)
    for header in (
        "# buy-smarter recommendation",
        "## Parsed request",
        "## Best pick",
        "## Alternatives",
        "## Top candidates",
        "## Б/у на OLX",
    ):
        assert header in md
    # Canonical model label surfaced in the used row.
    assert "Xiaomi Water Flosser 2 (MEO705)" in md


def test_save_writes_file_with_sections(tmp_path) -> None:
    target = tmp_path / "sub" / "report.md"
    written = save_report(_result(), "ирригатор до 40000", top=8, path=str(target))
    assert written == target
    text = target.read_text(encoding="utf-8")
    assert "## Best pick" in text
    assert "## Top candidates" in text


def test_alternatives_and_top_tables_link_the_product_url() -> None:
    md = render_markdown(_result(), top=8)
    assert "[Ирригатор 0](https://kaspi.kz/p/0)" in md
    assert "[Ирригатор 4](https://kaspi.kz/p/4)" in md


def test_product_without_url_renders_plain_title() -> None:
    result = _result(n_ranked=1)
    result.recommendation.best.candidate.url = None
    result.recommendation.ranked[0].candidate.url = None
    md = render_markdown(result, top=8)
    assert "| 1 | kaspi | Ирригатор 0 |" in md


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

    assert "| Waterpik WP-660 б/у | 18,000 ₸ | — |" in md


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

    assert "- **Required:** usb-c" in md
    assert "- **Excluded:** noname" in md


def test_best_md_shows_review_penalty_and_cons() -> None:
    result = _result()
    best = result.recommendation.best
    best.review_penalty = 7.0
    best.cons = ["шумный"]

    md = render_markdown(result)

    assert "- **Penalty:** отзывы (−7)" in md
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

    assert "| Best budget |" in md
    assert "| Best value |" not in md


def test_no_substance_markdown_lists_alternatives_with_rating() -> None:
    rated = Candidate(source="kaspi", source_product_id="1", title="Крем А", price=5000, rating=4.5)
    unrated = Candidate(source="kaspi", source_product_id="2", title="Крем Б", price=3000)
    exc = NoSubstanceMatch(["ретинол"], checked=4, alternatives=[rated, unrated])

    md = render_no_substance_markdown(exc)

    assert "ретинол" in md
    assert "| Product | Price | Rating |" in md
    assert "| 4.5 |" in md
    assert "| Крем Б |" in md and md.rstrip().endswith("| - |")


def test_no_substance_markdown_without_alternatives_has_no_table() -> None:
    exc = NoSubstanceMatch(["ретинол"], checked=0, alternatives=[])

    assert "| Product |" not in render_no_substance_markdown(exc)
