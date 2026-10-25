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
    rows = [[axes.item(r, c).text() for c in (0, 1, 3)] for r in range(axes.rowCount())]
    assert rows == [
        ["Цена", "20.0/25", "ниже медианы рынка"],
        ["Характеристики", "18.0/20", "IPX7 есть"],
    ]
    assert [axes.cellWidget(r, 2).value() for r in range(2)] == [80, 90]  # the bars
    labels = _texts(screen).splitlines()
    for text in ("Philips Sonicare Power Flosser", "Kaspi", "88/100", "80%", "38\xa0000 ₸"):
        assert text in labels
    assert {"Баллы", "Уверенность", "Цена"} <= set(labels)


def test_alternatives_sort_by_price_and_filter_to_one_marketplace(qtbot, result):
    screen = _shown(qtbot, result)
    proxy = screen.alternatives_proxy
    proxy.sort(3, Qt.SortOrder.AscendingOrder)
    assert _column(proxy, 3) == ["19\xa0000 ₸", "35\xa0000 ₸", "38\xa0000 ₸"]
    assert _column(proxy, 0) == ["Дешевле", "", "Лучший выбор"]

    screen.marketplace.setCurrentText("Ozon")
    assert _column(proxy, 2) == ["Waterpik WP-660 Aquarius"]
    screen.marketplace.setCurrentText("Все маркетплейсы")
    assert proxy.rowCount() == 3


def test_alternatives_sort_by_an_axis_score(qtbot, result):
    screen = _shown(qtbot, result)
    proxy = screen.alternatives_proxy
    headers = [proxy.headerData(c, Qt.Orientation.Horizontal) for c in range(proxy.columnCount())]
    specs_col = headers.index("Характеристики")
    proxy.sort(specs_col, Qt.SortOrder.DescendingOrder)
    assert _column(proxy, specs_col) == ["18.0/20", "15.0/20", "12.0/20"]


def test_rejected_used_and_could_not_verify_sections(qtbot, result):
    run = SearchRun("ирригатор", False, {"kaspi": "done", "ozon": "skipped", "olx": "done"})
    screen = _shown(qtbot, result, run)
    text = _texts(screen)
    assert "Отсеяно жёсткими фильтрами: 3." in text
    assert "дороже бюджета ×2, исключённый бренд ×1" in text
    assert screen.used.item(0, 0).text() == "ирригатор xiaomi б/у"
    assert screen.used.item(0, 2).text() == "10\xa0000 ₸"  # a saving, no minus
    assert "Ozon: пропущен" in text
    assert "AI выключен" in text


def test_a_keyless_run_says_ai_is_not_configured_and_where_to_add_a_key(qtbot, result):
    result = result.model_copy(update={"skipped_llm_steps": ["query parsing"]})
    run = SearchRun("ирригатор", True, {"kaspi": "done", "ozon": "done", "olx": "done"})
    text = _texts(_shown(qtbot, result, run))
    assert "AI не настроен" in text
    assert "Ключ можно добавить в «Состояние и ключи»" in text


def test_ozon_without_a_browser_says_so_and_points_to_settings(qtbot, result):
    result = result.model_copy(update={"unavailable_sources": ["ozon"]})
    run = SearchRun("ирригатор", True, {"kaspi": "done", "ozon": "skipped", "olx": "done"})
    text = _texts(_shown(qtbot, result, run))
    assert "Ozon: пропущен — нет браузера; что сделать, смотрите в «Состояние и ключи»" in text
    assert "не выбран или поиск до него не дошёл" not in text


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


def test_roles_follow_the_marketplace_when_ids_collide(qtbot, result):
    from tests.gui.conftest import scored

    twin = scored("ozon", "3", "Panasonic EW1411", 21000, 60.0, 10.0)  # same id as kaspi's "3"
    rec = result.recommendation
    rec = rec.model_copy(update={"ranked": [*rec.ranked, twin]})
    screen = _shown(qtbot, result.model_copy(update={"recommendation": rec}))
    proxy = screen.alternatives_proxy
    roles = {proxy.index(r, 2).data(): proxy.index(r, 0).data() for r in range(proxy.rowCount())}
    assert roles["Xiaomi Water Flosser 2"] == "Дешевле"
    assert roles["Panasonic EW1411"] == ""


def test_a_category_without_knowledge_offers_to_learn_it_then_to_search_again(
    qtbot, runner, result
):
    learned = []

    async def learn(category):
        learned.append(category)

    result = result.model_copy(update={"knowledge_missing": True})
    screen = ReportScreen(runner, learn=learn)
    qtbot.addWidget(screen)
    screen.show_result(result, _ALL_RAN)
    assert "нет данных о брендах" in _texts(screen)

    screen.learn_button.click()
    qtbot.waitUntil(lambda: screen.learn_button.text() == "Повторить поиск", timeout=3000)
    assert learned == ["ирригатор"]
    with qtbot.waitSignal(screen.rerun_requested, timeout=1000):
        screen.learn_button.click()


def test_no_learn_offer_when_the_category_is_known(qtbot, result):
    screen = _shown(qtbot, result)
    assert screen.learn_button is None


def test_nothing_rejected_shows_no_rejected_section(qtbot, result):
    rec = result.recommendation.model_copy(update={"rejected_count": 0, "rejected_reasons": {}})
    screen = _shown(qtbot, result.model_copy(update={"recommendation": rec}))
    assert screen.rejected is None


def test_a_missing_review_summary_says_why(qtbot, result):
    result = result.model_copy(update={"review_summary_note": "мало текстов отзывов для сводки"})
    assert "Сводка отзывов недоступна: мало текстов отзывов для сводки" in _texts(
        _shown(qtbot, result)
    )


def test_the_review_summary_is_shown(qtbot, result):
    result = result.model_copy(update={"review_summary": _summary()})
    assert "+ тихий" in _texts(_shown(qtbot, result))


def test_axis_columns_start_hidden_and_toggle_on(qtbot, result):
    from buysmarter.gui.screens.alternatives import AXES_START

    screen = _shown(qtbot, result)
    view = screen.alternatives_view
    assert view.isColumnHidden(AXES_START)
    screen.show_axes.setChecked(True)
    assert not view.isColumnHidden(AXES_START)


def test_bar_levels_flag_weak_and_strong_axes():
    from buysmarter.gui.screens.report import bar_level

    assert (bar_level(3.8, 25), bar_level(10, 20), bar_level(10, 10)) == ("low", "", "high")


def test_unchecked_composition_is_listed_under_could_not_verify(qtbot, result):
    result = result.model_copy(update={"ingredients_unchecked": True})
    total = len(result.recommendation.ranked)
    text = _texts(_shown(qtbot, result))
    assert "Не удалось проверить" in text
    assert f"Состав не проверен у {total} из {total} — вещество не подтверждено" in text


def test_the_report_says_how_it_was_assessed(qtbot, result):
    from buysmarter.capabilities.provenance import CapabilityState

    state = CapabilityState(
        name="benchmark",
        label="Бенчмарк Kimovil",
        level="category",
        on=True,
        active=True,
        auto=True,
        set_by="jev",
    )

    screen = _shown(qtbot, result.model_copy(update={"capabilities": [state]}))

    assert "Как оценивали: бенчмарк Kimovil — Jev." in _texts(screen)


def test_hidden_suspected_fakes_get_a_section_with_their_titles(qtbot, result):
    fake = result.recommendation.ranked[2]
    text = _texts(_shown(qtbot, result.model_copy(update={"hidden": [fake]})))
    assert "Скрыто подозрительных предложений (только оригинал): 1" in text
    assert "Xiaomi Water Flosser 2" in text


def test_no_hidden_section_without_hidden_ones(qtbot, result):
    assert "Скрыто" not in _texts(_shown(qtbot, result))


def _group_titles(screen) -> list[str]:
    from PySide6.QtWidgets import QGroupBox

    layout = screen.widget().layout()
    items = (layout.itemAt(i).widget() for i in range(layout.count()))
    return [w.title() for w in items if isinstance(w, QGroupBox)]


def test_a_used_query_shows_the_used_section_first(qtbot, result):
    constraints = result.constraints.model_copy(update={"condition": "used"})
    screen = _shown(qtbot, result.model_copy(update={"constraints": constraints}))

    assert _group_titles(screen)[0] == "На OLX (частники)"


def test_a_new_query_shows_the_used_section_after_the_ranking(qtbot, result):
    titles = _group_titles(_shown(qtbot, result))

    assert titles.index("На OLX (частники)") > 0


def test_configure_link_at_the_top_scrolls_to_the_category_switches(qtbot, result, monkeypatch):
    screen = _shown(qtbot, result)
    seen = []
    monkeypatch.setattr(screen, "ensureWidgetVisible", lambda widget, *a: seen.append(widget))

    screen.configure_link.click()

    assert screen.configure_link.text() == "Настроить"
    assert seen == [screen.toggles]


def test_a_summary_turned_off_on_the_form_is_not_reported_as_missing(qtbot, result):
    # as the CLI's --no-review-summary: the user asked for none
    run = SearchRun("ирригатор", True, _ALL_RAN.source_states, review_summary=False)
    assert "Сводка отзывов недоступна" not in _texts(_shown(qtbot, result, run))
