"""Report screen: best pick, alternatives sort/filter, sections, opening offers."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices

from buysmarter.gui.screens.report import ReportScreen
from buysmarter.gui.screens.search import SearchRun

_ALL_RAN = SearchRun("ирригатор", True, {"kaspi": "done", "ozon": "done", "olx": "done"})


def _shown(qtbot, result, run=_ALL_RAN) -> ReportScreen:
    screen = ReportScreen()
    qtbot.addWidget(screen)
    screen.show_result(result, run)
    return screen


def _texts(screen) -> str:
    from PySide6.QtWidgets import QGroupBox, QLabel

    labels = [w.text() for w in screen.widget().findChildren(QLabel)]
    titles = [g.title() for g in screen.widget().findChildren(QGroupBox)]
    return "\n".join(labels + titles)


def _column(proxy, col: int) -> list[str]:
    return [proxy.index(r, col).data() for r in range(proxy.rowCount())]


def test_best_pick_shows_each_axis_as_score_over_max_with_its_reason(qtbot, result):
    screen = _shown(qtbot, result)
    axes = screen.best_axes
    rows = [[axes.item(r, c).text() for c in range(3)] for r in range(axes.rowCount())]
    assert rows == [["price", "20.0/25", "ниже медианы рынка"], ["specs", "18.0/20", "IPX7 есть"]]
    text = _texts(screen)
    assert "Philips Sonicare Power Flosser  [kaspi]" in text
    assert "Score: 88/100   Confidence: 80%" in text and "Price: 38,000 ₸" in text


def test_alternatives_sort_by_price_and_filter_to_one_marketplace(qtbot, result):
    screen = _shown(qtbot, result)
    proxy = screen.alternatives_proxy
    proxy.sort(3, Qt.SortOrder.AscendingOrder)
    assert _column(proxy, 3) == ["19,000 ₸", "35,000 ₸", "38,000 ₸"]
    assert _column(proxy, 0) == ["Best budget", "", ""]

    screen.marketplace.setCurrentText("ozon")
    assert _column(proxy, 2) == ["Waterpik WP-660 Aquarius"]
    screen.marketplace.setCurrentText("Все маркетплейсы")
    assert proxy.rowCount() == 3


def test_alternatives_sort_by_an_axis_score(qtbot, result):
    screen = _shown(qtbot, result)
    proxy = screen.alternatives_proxy
    headers = [proxy.headerData(c, Qt.Orientation.Horizontal) for c in range(proxy.columnCount())]
    specs_col = headers.index("specs")
    proxy.sort(specs_col, Qt.SortOrder.DescendingOrder)
    assert _column(proxy, specs_col) == ["18.0/20", "15.0/20", "12.0/20"]


def test_rejected_used_and_could_not_verify_sections(qtbot, result):
    run = SearchRun("ирригатор", False, {"kaspi": "done", "ozon": "skipped", "olx": "done"})
    screen = _shown(qtbot, result, run)
    text = _texts(screen)
    assert "3 candidate(s) rejected by hard filters." in text
    assert "Отсеяно: дороже бюджета ×2, исключённый бренд ×1" in text
    assert screen.used.item(0, 0).text() == "ирригатор xiaomi б/у"
    assert screen.used.item(0, 2).text() == "-10,000 ₸"
    assert "ozon: пропущен" in text
    assert "AI выключен" in text


def test_could_not_verify_is_hidden_when_everything_ran(qtbot, result):
    result = result.model_copy(update={"review_summary": _summary()})
    assert "Не удалось проверить" not in _texts(_shown(qtbot, result))


def test_activating_offers_opens_them_in_the_system_browser(qtbot, result, monkeypatch):
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    screen = _shown(qtbot, result)
    screen.open_best.click()
    screen.alternatives_view.doubleClicked.emit(screen.alternatives_proxy.index(1, 2))
    screen.used.cellDoubleClicked.emit(0, 0)
    assert opened == [
        "https://kaspi.kz/p/1",
        "https://ozon.kz/p/2",
        "https://olx.kz/d/1",
    ]


def _summary():
    from buysmarter.llm.review_summary import ReviewSummary

    return ReviewSummary(pros=["тихий"])


def test_alternatives_start_in_ranking_order(qtbot, result):
    proxy = _shown(qtbot, result).alternatives_proxy
    assert _column(proxy, 4) == ["88", "81", "74"]
