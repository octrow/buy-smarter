"""Turn the core's stage log records into a Qt progress signal, with no core API change."""

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot

_CORE_LOGGER = "buysmarter"


class Progress(QObject):
    """While attached, emits ``changed(source, state, stage)`` for each core stage record.

    ``source`` is the marketplace for a collect stage and ``""`` for a pipeline stage; ``state``
    is running, done or failed. Emitted on the GUI thread.
    """

    changed = Signal(str, str, str)
    _record = Signal(str, str, str)  # loop thread -> GUI thread hop

    def __init__(self) -> None:
        super().__init__()
        self._record.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self._handler = _StageHandler(self._record.emit)
        self._saved_level = logging.NOTSET

    def attach(self) -> None:
        core = logging.getLogger(_CORE_LOGGER)
        self._saved_level = core.level
        # Stage records are INFO; the file and console handlers keep their own levels.
        if core.getEffectiveLevel() > logging.INFO:
            core.setLevel(logging.INFO)
        core.addHandler(self._handler)

    def detach(self) -> None:
        core = logging.getLogger(_CORE_LOGGER)
        core.removeHandler(self._handler)
        core.setLevel(self._saved_level)

    @Slot(str, str, str)
    def _deliver(self, source: str, state: str, stage: str) -> None:
        self.changed.emit(source, state, stage)


class _StageHandler(logging.Handler):
    # ponytail: sees every core record while attached, so two concurrent jobs would mix their
    # progress; give each job its own context (contextvar filter) if the GUI ever runs two.
    def __init__(self, emit: Callable[[str, str, str], object]) -> None:
        super().__init__(logging.INFO)
        self._emit_progress = emit

    def emit(self, record: logging.LogRecord) -> None:
        state = getattr(record, "progress_state", None)
        if state is None:
            return
        source = getattr(record, "progress_source", None) or ""
        try:
            self._emit_progress(source, state, getattr(record, "progress_stage", ""))
        except Exception:  # noqa: BLE001 - a dead progress widget must not break the core's logging
            self.handleError(record)
