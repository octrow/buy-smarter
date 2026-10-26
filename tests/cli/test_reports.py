"""``buysmarter reports``: past searches, as «Прошлые поиски» and «Повторить» (#28, #29)."""

from typer.testing import CliRunner

from buysmarter.cli import main
from buysmarter.cli.report import report_parts, report_text, save_report, saved_reports

runner = CliRunner()
_QUERY = "Смартфон до 200 000, Алматы!"


def _saved(result, query: str = _QUERY):
    return save_report(result, query, 3, None)


def test_a_saved_report_keeps_the_whole_query_and_hides_it_from_the_reader(
    recommend_result,
) -> None:
    path = _saved(recommend_result)

    assert report_parts(path)[1] == _QUERY  # the file name would give "смартфон до 200 000 алматы"
    assert "query" not in report_text(path) and report_text(path).startswith("# buy-smarter")


def test_an_old_report_without_the_line_reads_the_query_from_its_name(tmp_path) -> None:
    old = tmp_path / "20261021-203800-смартфон-до-200000.md"
    old.write_text("# buy-smarter: рекомендация\n", encoding="utf-8")

    assert report_parts(old) == ("21.10.2026 20:38", "смартфон до 200000")


def test_reports_lists_the_past_searches_newest_first(recommend_result) -> None:
    _saved(recommend_result, "ирригатор")
    _saved(recommend_result, _QUERY)

    out = runner.invoke(main.app, ["reports"]).stdout

    assert out.index(_QUERY) < out.index("ирригатор")
    assert "│ 1 " in out and "│ 2 " in out  # the numbers show and repeat take


def test_reports_show_prints_one_report(recommend_result) -> None:
    _saved(recommend_result)

    shown = runner.invoke(main.app, ["reports", "show", "1"])

    assert shown.exit_code == 0
    assert "Лучший выбор" in shown.stdout and "query" not in shown.stdout


def test_reports_repeat_searches_the_saved_query_again(monkeypatch, recommend_result) -> None:
    _saved(recommend_result)
    asked = []
    monkeypatch.setattr(main, "recommend", lambda **kw: asked.append(kw["query"]))

    repeated = runner.invoke(main.app, ["reports", "repeat", "1"])

    assert repeated.exit_code == 0, repeated.output
    assert asked == [_QUERY]


def test_a_number_past_the_list_is_refused(recommend_result) -> None:
    _saved(recommend_result)

    assert runner.invoke(main.app, ["reports", "show", "2"]).exit_code == 2
    assert len(saved_reports()) == 1
