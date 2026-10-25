"""The search screen asks back: option buttons, own text, the next round (phase 2, step 9)."""

from buysmarter.gui.screens.report import ReportScreen
from buysmarter.gui.screens.search import SearchRun, SearchScreen
from buysmarter.services.understanding import Clarification, NeedsClarification, NothingToSearch
from tests.gui.conftest import WAIT_MS

_GIFT = Clarification(
    kind="open",
    question="Что именно ищем?",
    options=["духи", "плед"],
    restricted_note="Алкоголь: доставка ограничена.",
)
_RAN = SearchRun("подарок маме", True, {"kaspi": "done", "ozon": "done", "olx": "done"})


def _asks_once(result, calls):
    async def recommend(query, **kwargs):
        calls.append((query, kwargs))
        if len(calls) == 1:
            raise NeedsClarification(_GIFT)
        return result

    return recommend


def _asked(qtbot, runner, result, calls) -> SearchScreen:
    screen = SearchScreen(runner, recommend=_asks_once(result, calls))
    qtbot.addWidget(screen)
    screen.query.setText("подарок маме")
    screen.start()
    qtbot.waitUntil(lambda: screen.clarify.isVisibleTo(screen), timeout=WAIT_MS)
    return screen


def test_a_question_shows_its_options_and_warning(qtbot, runner, result) -> None:
    screen = _asked(qtbot, runner, result, [])

    assert screen.clarify.question.text() == "Что именно ищем?"
    assert [b.text() for b in screen.clarify.option_buttons] == ["духи", "плед"]
    assert "Алкоголь" in screen.clarify.warning.text()
    assert screen.start_button.isEnabled()
    assert not screen.recent_box.isVisibleTo(screen)


def test_a_second_question_replaces_the_first_options(qtbot, runner, result) -> None:
    screen = _asked(qtbot, runner, result, [])

    screen.clarify.ask(Clarification(kind="multi", question="С чего начнём?", options=["A"]))

    assert [b.text() for b in screen.clarify.option_buttons] == ["A"]
    assert screen.clarify._options.count() == 1
    assert not screen.clarify.warning.isVisibleTo(screen)


def test_long_options_do_not_widen_the_search_screen(qtbot, runner, result) -> None:
    screen = _asked(qtbot, runner, result, [])
    long_options = [
        "Электрическая зубная щётка с футляром",
        "Набор косметики для ухода за лицом",
        "Плед из шерсти мериноса 200x220",
        "Кофемашина капсульная с капучинатором",
        "Сертификат в магазин парфюмерии",
    ]

    screen.clarify.ask(Clarification(kind="open", question="Что ищем?", options=long_options))

    # 600 px: well under the app's default window (1280) minus the sidebar
    assert screen.clarify.minimumSizeHint().width() < 600
    assert screen.clarify.own.accessibleName() == "Свой ответ"


def test_an_option_button_searches_it_in_the_next_round(qtbot, runner, result) -> None:
    calls: list = []
    screen = _asked(qtbot, runner, result, calls)

    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        screen.clarify.option_buttons[1].click()

    query, kwargs = calls[1]
    assert query == "подарок маме"
    assert (kwargs["answer"], kwargs["clarify_round"]) == ("плед", 1)
    assert not screen.clarify.isVisibleTo(screen)


def test_an_own_answer_joins_the_query(qtbot, runner, result) -> None:
    calls: list = []
    screen = _asked(qtbot, runner, result, calls)

    screen.clarify.own.setText("шарф кашемировый")
    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        screen.clarify.own.returnPressed.emit()

    query, kwargs = calls[1]
    assert query == screen.query.text() == "подарок маме шарф кашемировый"
    assert (kwargs.get("answer"), kwargs["clarify_round"]) == (None, 1)


def test_a_new_search_starts_again_from_round_zero(qtbot, runner, result) -> None:
    calls: list = []
    screen = _asked(qtbot, runner, result, calls)

    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        screen.start()

    assert "clarify_round" not in calls[1][1]
    assert not screen.clarify.isVisibleTo(screen)


def test_nothing_to_search_is_shown_as_is(qtbot, runner) -> None:
    async def service(query, **kwargs):
        raise NothingToSearch("Это услуга, а не товар.")

    screen = SearchScreen(runner, recommend=service)
    qtbot.addWidget(screen)
    screen.query.setText("ремонт телефона")
    screen.start()
    qtbot.waitUntil(screen.start_button.isEnabled, timeout=WAIT_MS)

    assert screen.message.text() == "Это услуга, а не товар."


def _labels(screen) -> list[str]:
    from PySide6.QtWidgets import QLabel

    return [label.text() for label in screen.widget().findChildren(QLabel)]


def test_the_report_opens_with_the_restricted_warning_and_what_it_assumed(qtbot, result):
    result = result.model_copy(
        update={"restricted_note": "Алкоголь: доставка ограничена.", "assumed": "вино"}
    )
    screen = ReportScreen()
    qtbot.addWidget(screen)
    screen.show_result(result, _RAN)

    texts = _labels(screen)
    assert texts[0] == "⚠ Алкоголь: доставка ограничена."
    assert any("Искал: вино" in t for t in texts)


def test_learning_a_category_learns_the_product_type(qtbot, runner, result) -> None:
    learned: list[str] = []

    async def learn(category):
        learned.append(category)

    constraints = result.constraints.model_copy(
        update={"search_phrase": "мужские кроссовки", "product_type": "кроссовки"}
    )
    shown = result.model_copy(update={"knowledge_missing": True, "constraints": constraints})
    screen = ReportScreen(runner, learn=learn)
    qtbot.addWidget(screen)
    screen.show_result(shown, _RAN)

    screen.learn_button.click()
    qtbot.waitUntil(lambda: learned == ["кроссовки"], timeout=WAIT_MS)


def test_a_rerun_after_learning_keeps_the_answer_and_the_round(qtbot, runner, result) -> None:
    """Review: «Изучить категорию» then a rerun asked the answered question again."""
    from buysmarter.gui import app as gui_app

    calls: list = []
    window = gui_app.MainWindow(runner)
    qtbot.addWidget(window)
    window.search._recommend = _asks_once(result, calls)
    window.search.query.setText("подарок маме")
    window.search.start()
    qtbot.waitUntil(lambda: window.search.clarify.isVisibleTo(window.search), timeout=WAIT_MS)
    with qtbot.waitSignal(window.search.report_ready, timeout=WAIT_MS):
        window.search.clarify.option_buttons[0].click()

    with qtbot.waitSignal(window.search.report_ready, timeout=WAIT_MS):
        window.report.rerun_requested.emit()

    _query, kwargs = calls[2]
    assert (kwargs["answer"], kwargs["clarify_round"]) == ("духи", 1)


def test_search_without_questions_repeats_the_search_with_no_clarify_as_enter_in_the_cli(
    qtbot, runner, result
) -> None:
    calls: list = []
    screen = _asked(qtbot, runner, result, calls)

    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        screen.clarify.skip_button.click()

    query, kwargs = calls[1]
    assert query == "подарок маме"
    assert kwargs["no_clarify"] is True and kwargs["clarify_round"] == 1
    assert "answer" not in kwargs
    assert not screen.clarify.isVisibleTo(screen)


def test_a_repeat_after_learning_keeps_asking_nothing(qtbot, runner, result) -> None:
    calls: list = []
    screen = _asked(qtbot, runner, result, calls)
    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        screen.clarify.skip_button.click()

    with qtbot.waitSignal(screen.report_ready, timeout=WAIT_MS):
        screen.rerun()

    assert calls[2][1]["no_clarify"] is True
