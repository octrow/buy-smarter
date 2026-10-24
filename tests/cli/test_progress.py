"""The search's stages in the terminal, as the desktop app's progress line (CLI <-> GUI #21)."""

import io
import logging

from rich.console import Console

from buysmarter.cli.progress import stages
from buysmarter.i18n import STAGE_LABELS
from buysmarter.services.collect import stage

CORE = logging.getLogger("buysmarter")


def _terminal() -> tuple[Console, io.StringIO]:
    out = io.StringIO()
    return Console(file=out, force_terminal=True, width=80), out


def test_each_core_stage_shows_in_russian_on_a_terminal() -> None:
    console, out = _terminal()

    with stages(console), stage("ranking"):
        pass

    assert STAGE_LABELS["ranking"] in out.getvalue()


def test_the_stages_reach_the_terminal_even_when_the_logs_keep_only_warnings() -> None:
    console, out = _terminal()
    CORE.setLevel(logging.WARNING)
    try:
        with stages(console), stage("collect", source="kaspi"):
            pass
        assert CORE.level == logging.WARNING  # put back
    finally:
        CORE.setLevel(logging.NOTSET)

    assert STAGE_LABELS["collect"] in out.getvalue()


def test_off_a_terminal_nothing_is_shown_and_nothing_stays_attached() -> None:
    out = io.StringIO()
    handlers = list(CORE.handlers)

    with stages(Console(file=out, force_terminal=False)), stage("ranking"):
        pass

    assert out.getvalue() == ""
    assert CORE.handlers == handlers
