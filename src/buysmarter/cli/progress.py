"""The search's stages in the terminal, from the core's stage records, as the app's progress line.

``services.collect.stage`` logs each stage with ``progress_*`` extra fields (``gui/progress``
reads the same records); here they drive a rich status line. Off a terminal nothing is shown.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console
from rich.status import Status

from buysmarter.i18n import stage_label

_CORE_LOGGER = "buysmarter"
_STARTING = "Запускаю поиск…"


class _Stages(logging.Handler):
    def __init__(self, status: Status) -> None:
        super().__init__(logging.INFO)
        self._status = status

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "progress_state", None) == "running":
            self._status.update(f"{stage_label(getattr(record, 'progress_stage', ''))}…")


@contextmanager
def stages(console: Console) -> Iterator[None]:
    """Show the running stage of the search inside this block, on a terminal only."""
    if not console.is_terminal:
        yield
        return
    core = logging.getLogger(_CORE_LOGGER)
    level = core.level
    # Stage records are INFO; the file and console handlers keep their own levels.
    if core.getEffectiveLevel() > logging.INFO:
        core.setLevel(logging.INFO)
    with console.status(_STARTING) as status:
        handler = _Stages(status)
        core.addHandler(handler)
        try:
            yield
        finally:
            core.removeHandler(handler)
            core.setLevel(level)
