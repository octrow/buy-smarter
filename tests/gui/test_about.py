"""«О программе»: a manual check enables «Обновить»; auto-update takes what it finds."""

from buysmarter.gui.screens.about import AboutScreen
from buysmarter.services import update
from buysmarter.services.update import Status, UpdateError
from tests.gui.conftest import WAIT_MS

_NEW = Status("0.11.1 (abc1234)", "def5678", True, True, "Доступно обновление: def5678.")


def _screen(qtbot, runner, check, apply=lambda settings, install: "Обновлено до def5678."):
    screen = AboutScreen(runner, install="checkout", check=check, apply=apply)
    qtbot.addWidget(screen)
    return screen


def test_a_manual_check_shows_the_news_and_enables_update(qtbot, runner):
    screen = _screen(qtbot, runner, lambda settings, install: _NEW)
    screen.check_now()
    qtbot.waitUntil(screen.update_button.isEnabled, timeout=WAIT_MS)
    assert screen.status.text() == _NEW.note
    assert "0.11.1 (abc1234)" in screen.version_label.text()
    assert screen.restart_button.isHidden()


def test_auto_update_applies_and_asks_for_a_restart(qtbot, runner):
    update.save_settings(update.UpdateSettings(channel="code", auto=True))
    screen = _screen(qtbot, runner, lambda settings, install: _NEW)
    with qtbot.waitSignal(screen.update_ready, timeout=WAIT_MS):
        screen.auto_tick()
    assert not screen.restart_button.isHidden()
    assert screen.status.text().startswith("Обновлено")

    screen.auto_tick()
    assert screen._job is None  # taken already: no second check before the restart


def test_auto_off_never_checks(qtbot, runner):
    update.save_settings(update.UpdateSettings(channel="code", auto=False))
    screen = _screen(qtbot, runner, lambda settings, install: _NEW)
    screen.auto_tick()
    assert screen._job is None


def test_an_update_error_is_shown_as_written(qtbot, runner):
    def offline(settings, install):
        raise UpdateError("GitHub недоступен: проверьте интернет.")

    screen = _screen(qtbot, runner, offline)
    screen.check_now()
    qtbot.waitUntil(lambda: "недоступен" in screen.status.text(), timeout=WAIT_MS)
    assert screen.check_button.isEnabled() and not screen.update_button.isEnabled()
