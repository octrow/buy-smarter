"""Turn the core's stage log records into a Qt progress signal, with no core API change."""

import contextvars
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot

_CORE_LOGGER = "buysmarter"

type _Emit = Callable[[str, str, str], object]

# The Progress of the job whose task logged a record. asyncio copies the context into the tasks
# a job spawns (gather, to_thread), so two jobs running at once never see each other's stages.
_ACTIVE: contextvars.ContextVar[_Emit | None] = contextvars.ContextVar("progress", default=None)


class Progress(QObject):
    """Emits ``changed(source, state, stage)`` for each core stage record of a tracked job.

    ``source`` is the marketplace for a collect stage and ``""`` for a pipeline stage; ``state``
    is running, done, failed or cancelled. Emitted on the GUI thread. Keep the Progress alive as
    long as its screen: the loop thread holds its emitter while a tracked job runs.
    """

    changed = Signal(str, str, str)
    _record = Signal(str, str, str)  # loop thread -> GUI thread hop

    def __init__(self) -> None:
        super().__init__()
        self._record.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        _install_handler()

    async def track[T](self, coro: Coroutine[Any, Any, T]) -> T:
        """Run ``coro`` (on the core loop) with its stage records routed to this Progress."""
        token = _ACTIVE.set(self._record.emit)
        try:
            return await coro
        finally:
            _ACTIVE.reset(token)

    @Slot(str, str, str)
    def _deliver(self, source: str, state: str, stage: str) -> None:
        self.changed.emit(source, state, stage)


class _StageHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        state = getattr(record, "progress_state", None)
        if state is None or (emit_progress := _ACTIVE.get()) is None:
            return
        source = getattr(record, "progress_source", None) or ""
        try:
            emit_progress(source, state, getattr(record, "progress_stage", ""))
        except Exception:  # noqa: BLE001 - a dead progress widget must not break the core's logging
            self.handleError(record)


def _install_handler() -> None:
    """Attach the one stage handler to the core logger, once."""
    core = logging.getLogger(_CORE_LOGGER)
    if any(isinstance(h, _StageHandler) for h in core.handlers):
        return
    # Stage records are INFO; the file and console handlers keep their own levels.
    if core.getEffectiveLevel() > logging.INFO:
        core.setLevel(logging.INFO)
    core.addHandler(_StageHandler(logging.INFO))
