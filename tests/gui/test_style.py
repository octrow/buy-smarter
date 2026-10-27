"""«Стиль»: the clothing-taste profile, as ``buysmarter style`` (plan В9)."""

import asyncio

from buysmarter import style
from buysmarter.cli import style as style_cli
from buysmarter.gui import app as gui_app
from buysmarter.gui.screens.style import StyleScreen
from buysmarter.llm.adapter import LlmError
from buysmarter.style import profile
from tests.gui.conftest import WAIT_MS


def _profile() -> profile.Profile:
    return asyncio.run(profile.load())


def _screen(qtbot, runner, picked=()) -> StyleScreen:
    screen = StyleScreen(runner, pick_photos=lambda _parent: [str(p) for p in picked])
    qtbot.addWidget(screen)
    screen.show()
    qtbot.waitUntil(lambda: screen.loaded, timeout=WAIT_MS)
    return screen


def _texts(widget) -> list[str]:
    return [widget.item(i).text() for i in range(widget.count())]


def test_the_window_has_a_style_screen(qtbot, runner):
    window = gui_app.MainWindow(runner)
    qtbot.addWidget(window)

    assert isinstance(window.style_profile, StyleScreen)
    titles = [window.sidebar.item(i).text() for i in range(window.sidebar.count())]
    assert "Стиль" in titles


def test_the_screen_shows_what_the_cli_saved(qtbot, runner, photo, encoder):
    asyncio.run(profile.set_brands(["Salomon", "New Balance"]))
    asyncio.run(profile.add_phrase("glossy white plastic sole", never=True))
    asyncio.run(profile.add_photos([photo()], disliked=False))

    screen = _screen(qtbot, runner)

    assert screen.brands.text() == "Salomon, New Balance"
    assert _texts(screen.phrases) == ["Нет: glossy white plastic sole"]
    assert screen.photos.count() == 1
    assert style_cli.photo_count(_profile().photos) in screen.photo_summary.text()


def test_saved_brands_are_what_the_cli_stores(qtbot, runner):
    screen = _screen(qtbot, runner)
    screen.brands.setText("Salomon,  Arc'teryx ,")

    screen.save_brands.click()

    qtbot.waitUntil(lambda: screen.message.text() == style_cli.SAVED, timeout=WAIT_MS)
    assert _profile().brands == ["Salomon", "Arc'teryx"]


def test_a_phrase_is_added_to_never_and_removed_again(qtbot, runner):
    screen = _screen(qtbot, runner)
    screen.phrase.setText("glossy white plastic sole")

    screen.never_button.click()
    qtbot.waitUntil(lambda: screen.phrases.count() == 1, timeout=WAIT_MS)
    assert screen.phrase.text() == ""
    assert [p.text for p in _profile().nevers] == ["glossy white plastic sole"]

    screen.phrases.setCurrentRow(0)
    screen.remove_phrase_button.click()
    qtbot.waitUntil(lambda: screen.phrases.count() == 0, timeout=WAIT_MS)
    assert _profile().nevers == []


def test_a_russian_phrase_without_the_llm_says_why(qtbot, runner, monkeypatch):
    async def down(text):
        raise LlmError("no route")

    monkeypatch.setattr(profile, "translate", down)
    screen = _screen(qtbot, runner)
    screen.phrase.setText("белая пластиковая подошва")

    screen.like_button.click()

    qtbot.waitUntil(lambda: screen.message.text() == style_cli.UNTRANSLATED, timeout=WAIT_MS)
    assert screen.phrase.text() == "белая пластиковая подошва"  # kept to rewrite in English
    assert _profile().likes == []


def test_photos_are_added_as_samples_and_as_never(qtbot, runner, photo, encoder):
    screen = _screen(qtbot, runner, picked=[photo("good.jpg"), photo("bad.jpg", "white")])

    screen.add_no_button.click()

    qtbot.waitUntil(lambda: screen.photos.count() == 2, timeout=WAIT_MS)
    assert all(p.disliked for p in _profile().photos)
    assert "0 «нравится», 2 «нет»" in screen.photo_summary.text()


def test_a_selected_photo_is_removed(qtbot, runner, photo, encoder):
    asyncio.run(profile.add_photos([photo("a.jpg"), photo("b.jpg", "olive")], disliked=False))
    screen = _screen(qtbot, runner)

    screen.photos.item(0).setSelected(True)
    screen.remove_photo_button.click()

    qtbot.waitUntil(lambda: screen.photos.count() == 1, timeout=WAIT_MS)
    assert [p.label for p in _profile().photos] == ["b.jpg"]


def test_a_cancelled_picker_adds_nothing(qtbot, runner, encoder):
    screen = _screen(qtbot, runner)

    screen.add_button.click()

    assert screen.photos.count() == 0
    assert screen.message.text() == ""


def test_without_the_extra_photos_cannot_be_added_and_the_screen_says_why(
    qtbot, runner, monkeypatch
):
    monkeypatch.setattr(style, "available", lambda: False)

    screen = _screen(qtbot, runner)

    assert not screen.add_button.isEnabled() and not screen.add_no_button.isEnabled()
    assert screen.no_extra.text() == style_cli.NO_EXTRA
    assert screen.no_extra.isVisibleTo(screen)


def test_with_the_extra_the_note_is_hidden(qtbot, runner, monkeypatch):
    monkeypatch.setattr(style, "available", lambda: True)

    screen = _screen(qtbot, runner)

    assert not screen.no_extra.isVisibleTo(screen)
    assert screen.add_button.isEnabled()


def test_a_folder_is_added_from_the_folder_button(qtbot, runner, tmp_path, photo, encoder):
    looks = tmp_path / "looks"
    looks.mkdir()
    photo("a.jpg").rename(looks / "a.jpg")
    screen = _screen(qtbot, runner)
    screen._pick_folder = lambda _parent: str(looks)  # the folder dialog

    screen.add_folder_button.click()

    qtbot.waitUntil(lambda: screen.photos.count() == 1, timeout=WAIT_MS)


def test_the_model_button_downloads_and_the_status_says_so(qtbot, runner, monkeypatch, tmp_path):
    from buysmarter.style import weights

    state = {"missing": ["vision"]}

    async def fake(client=None):
        state["missing"] = []
        return tmp_path

    monkeypatch.setattr(style, "available", lambda: True)
    monkeypatch.setattr(weights, "missing", lambda: state["missing"])
    monkeypatch.setattr(weights, "download", fake)
    screen = _screen(qtbot, runner)
    assert screen.download_button.isVisibleTo(screen)

    screen.download_button.click()

    qtbot.waitUntil(lambda: screen.model_status.text() == style_cli.MODEL_READY, timeout=WAIT_MS)
    assert not screen.download_button.isVisibleTo(screen)


def test_strength_is_saved_as_the_cli_stores_it(qtbot, runner):
    screen = _screen(qtbot, runner)
    assert screen.strength.value() == profile.STRENGTH_DEFAULT
    assert screen.strength.maximum() == profile.STRENGTH_MAX

    screen.strength.setValue(0)

    qtbot.waitUntil(lambda: _profile().strength == 0, timeout=WAIT_MS)
