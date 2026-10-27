"""The report's 👍/👎: a card's main photo becomes a style sample, as ``style like/dislike``."""

import asyncio
import io

import pytest

from buysmarter import style
from buysmarter.capabilities.provenance import CapabilityState
from buysmarter.cli import style as style_cli
from buysmarter.gui.screens.alternatives import CANDIDATE_ROLE
from buysmarter.gui.screens.report import ReportScreen
from buysmarter.gui.screens.search import SearchRun
from buysmarter.style import profile
from tests.gui.conftest import WAIT_MS

_RUN = SearchRun("мужские кроссовки", True, {"kaspi": "done"})


def _style_state(on: bool) -> CapabilityState:
    return CapabilityState(
        name="style", label="Стиль", level="category", on=on, active=on, auto=on, set_by="jev"
    )


def _with_photos(result, on: bool = True):
    for i, scored in enumerate(result.recommendation.ranked):
        scored.candidate.image_url = f"https://img.kaspi.kz/{i}.jpg"
    return result.model_copy(update={"capabilities": [_style_state(on)]})


def _shown(qtbot, runner, result) -> ReportScreen:
    screen = ReportScreen(runner)
    qtbot.addWidget(screen)
    screen.show_result(result, _RUN)
    return screen


@pytest.fixture
def served(monkeypatch):
    """Card photos without a network: records which were asked for."""
    image = pytest.importorskip("PIL.Image")
    asked: list[str] = []

    async def fetch(url):
        asked.append(url)
        out = io.BytesIO()
        image.new("RGB", (40, 40), "grey").save(out, "JPEG")
        return out.getvalue()

    monkeypatch.setattr(profile, "fetch_card_photo", fetch)
    return asked


def _photos() -> list[profile.Photo]:
    return asyncio.run(profile.load()).photos


def test_the_best_pick_is_liked_with_its_title(qtbot, runner, result, served, encoder):
    screen = _shown(qtbot, runner, _with_photos(result))

    screen.like_best.click()

    qtbot.waitUntil(lambda: bool(screen.style_message.text()), timeout=WAIT_MS)
    best = result.recommendation.best.candidate
    assert served == [best.image_url]
    assert [(p.label, p.disliked) for p in _photos()] == [(best.title, False)]
    assert screen.style_message.text() == style_cli.LIKED.format(best.title)


def test_a_selected_alternative_is_disliked(qtbot, runner, result, served, encoder):
    screen = _shown(qtbot, runner, _with_photos(result))
    view = screen.alternatives_view
    assert not screen.dislike_selected.isEnabled()  # nothing selected yet

    view.selectRow(1)
    picked = view.model().index(1, 0).data(CANDIDATE_ROLE)
    screen.dislike_selected.click()

    qtbot.waitUntil(lambda: bool(_photos()), timeout=WAIT_MS)
    assert [(p.label, p.disliked) for p in _photos()] == [(picked.title, True)]


def test_a_photo_that_cannot_be_fetched_says_so(qtbot, runner, result, encoder, monkeypatch):
    async def down(url):
        raise profile.NoCardPhoto(url)

    monkeypatch.setattr(profile, "fetch_card_photo", down)
    screen = _shown(qtbot, runner, _with_photos(result))

    screen.like_best.click()

    qtbot.waitUntil(lambda: bool(screen.style_message.text()), timeout=WAIT_MS)
    assert screen.style_message.text() == style_cli.NO_CARD_PHOTO
    assert _photos() == []


@pytest.mark.parametrize(("on", "extra"), [(False, True), (True, False)])
def test_no_buttons_without_style_in_the_category_or_the_extra(
    qtbot, runner, result, monkeypatch, on, extra
):
    monkeypatch.setattr(style, "available", lambda: extra)

    screen = _shown(qtbot, runner, _with_photos(result, on=on))

    assert screen.like_best is None and screen.dislike_selected is None
