"""Central logging setup: rotating file at INFO, console stays at WARNING.

Called once at each entry point (CLI, MCP server) so the rich CLI output isn't
polluted by INFO-level noise (LLM cache hits/misses, source fetch counts,
filter drops) while that same noise is captured durably in ``~/.cache/buysmarter/logs/``.
"""

import contextvars
import logging
import os
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_LOG_PATH = Path.home() / ".cache" / "buysmarter" / "logs" / "buysmarter.log"
_MAX_BYTES = 5 * 1024 * 1024  # 5MB
_BACKUP_COUNT = 5
_FORMAT = "%(asctime)s %(levelname)s [%(run_id)s] %(name)s:%(lineno)d: %(message)s"
_DEFAULT_FILE_LEVEL = "INFO"
_DEFAULT_CONSOLE_LEVEL = "WARNING"
_NOISY_LOGGERS = ("httpx", "mcp")  # seen at INFO in the log file
_RUN_ID_LEN = 8
_STREAM_ONLY = "-"  # BUYSMARTER_LOG_FILE value: no file, stderr only

_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="-")


def new_run_id() -> str:
    """Start a new correlation id for this run/command/tool call and return it."""
    rid = uuid.uuid4().hex[:_RUN_ID_LEN]
    _run_id.set(rid)
    return rid


class _RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id.get()
        return True


def _env_level(var: str, default: str) -> int:
    raw = os.environ.get(var)
    if raw is None:
        return logging.getLevelNamesMapping()[default]
    level = logging.getLevelName(raw.strip().upper())
    if isinstance(level, int):
        return level
    logging.getLogger(__name__).warning("Invalid %s=%r, using %s", var, raw, default)
    return logging.getLevelNamesMapping()[default]


# Marker attribute so re-running setup_logging() (e.g. re-imported entry
# points, or tests parametrizing the log path) replaces our handlers instead
# of stacking duplicates on the root logger.
_MARKER = "_buysmarter_logsetup"


def _ours(handler: logging.Handler, level: int) -> logging.Handler:
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.addFilter(_RunIdFilter())
    setattr(handler, _MARKER, True)
    return handler


def setup_logging(log_path: Path | str | None = None) -> None:
    """Attach a rotating file handler (INFO) + console handler (WARNING) to root.

    ``BUYSMARTER_LOG_FILE=-`` (the container image) drops the file: one stderr handler
    at the file level instead, so the container runtime collects the INFO records.

    Safe to call more than once: any handlers this function previously added
    are removed first, so re-configuring (e.g. with a different path) doesn't
    duplicate output.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _MARKER, False):
            root.removeHandler(handler)

    file_level = _env_level("BUYSMARTER_LOG_LEVEL", _DEFAULT_FILE_LEVEL)
    target = str(log_path or os.environ.get("BUYSMARTER_LOG_FILE") or _DEFAULT_LOG_PATH)
    if target == _STREAM_ONLY:
        handlers = [_ours(logging.StreamHandler(), file_level)]
    else:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
        )
        console_level = _env_level("BUYSMARTER_CONSOLE_LOG_LEVEL", _DEFAULT_CONSOLE_LEVEL)
        handlers = [_ours(file_handler, file_level), _ours(logging.StreamHandler(), console_level)]

    root.setLevel(min(h.level for h in handlers))
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    for handler in handlers:
        root.addHandler(handler)
