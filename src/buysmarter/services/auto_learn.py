"""When a request may start learning its category on its own (phase 3, decision 8).

Only the condition and the marks live here; the entry points (GUI, MCP, worker, CLI) decide
where to run it and start the learning. The GUI and the CLI learn a new category before its
first search (``NeedsLearning``, finding #26); MCP and the worker learn after the report.
"""

import asyncio
import logging
import re
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from claude_cli import claude_available

from buysmarter.config import get_settings
from buysmarter.knowledge.generate import learn_category
from buysmarter.knowledge.profile import has_books
from buysmarter.knowledge.store import load_knowledge, update_knowledge

logger = logging.getLogger(__name__)

LEARNING_STARTED_KEY = "learning_started_at"
LEARN_FAILED_KEY = "learn_failed_at"
_WRITER = "auto-learn"
_INTENTS = frozenset({"product", "need"})
_MAX_NAME_LEN = 60
_NAME_RE = re.compile(r"[\w -]+")  # letters, digits, space, hyphen (and "_", harmless)
_LEARNING_TTL = timedelta(minutes=15)  # a learn in progress; later it is taken as dead
_FAILED_TTL = timedelta(hours=24)
_WINDOW_S = 3600.0

# ponytail: the hourly counter is per process (a deque of start times); the worker and the
# GUI each count their own. Move it to Redis if a shared budget is ever needed.
_starts: deque[float] = deque()


def reset_limit() -> None:
    """Forget the starts counted so far (tests)."""
    _starts.clear()


class NeedsLearning(Exception):  # noqa: N818 - an outcome, not an error
    """The search stopped before any marketplace: learn ``category`` first, then search again."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def over_limit() -> bool:
    """The hourly limit of learning starts is used up (``0`` per hour: always)."""
    now = time.monotonic()
    while _starts and now - _starts[0] >= _WINDOW_S:
        _starts.popleft()
    return len(_starts) >= get_settings().auto_learn_per_hour


def limit_note() -> str:
    """Why a new category was not learned before its search: the hourly limit (or it is off)."""
    per_hour = get_settings().auto_learn_per_hour
    if per_hour <= 0:
        return "Автоизучение выключено."
    return f"Автоизучение на паузе: лимит {per_hour} в час исчерпан."


def _valid_name(product_type: str) -> bool:
    name = product_type.strip()
    return 0 < len(name) <= _MAX_NAME_LEN and _NAME_RE.fullmatch(name) is not None


def _younger_than(raw: dict[str, Any], key: str, ttl: timedelta) -> bool:
    """The record's ISO mark ``key`` is younger than ``ttl`` (a missing or broken mark: no)."""
    try:
        stamp = datetime.fromisoformat(str(raw[key]))
    except (KeyError, ValueError):
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return datetime.now(UTC) - stamp < ttl


async def should_auto_learn(
    product_type: str, *, intent: str, use_llm: bool, knowledge_missing: bool
) -> bool:
    """Every clause of decision 8; ``use_llm`` is the request's, ``intent`` the parsed one.

    ``knowledge_missing`` is the run's: no books were found for the request anywhere (own
    record, synonym, parent, related profile). Counts nothing: call
    ``count_start`` when the entry point really starts (or queues) the learning.
    """
    if not knowledge_missing or intent not in _INTENTS or not use_llm:
        return False
    if not _valid_name(product_type):
        return False
    if not claude_available() or over_limit():
        return False
    raw = await load_knowledge(product_type) or {}
    if has_books(raw):
        return False
    return not (
        _younger_than(raw, LEARNING_STARTED_KEY, _LEARNING_TTL)
        or _younger_than(raw, LEARN_FAILED_KEY, _FAILED_TTL)
    )


async def check_learn_first(
    product_type: str, *, intent: str, use_llm: bool, knowledge_missing: bool
) -> None:
    """Raise ``NeedsLearning`` when the category may learn now (``should_auto_learn``)."""
    if await should_auto_learn(
        product_type, intent=intent, use_llm=use_llm, knowledge_missing=knowledge_missing
    ):
        raise NeedsLearning(product_type)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def count_start() -> None:
    """Count one start against the hourly limit, in the process that decided it.

    The entry point calls it, not the runner: the worker decides in the recommend process
    and learns in a task that may run in another one.
    """
    _starts.append(time.monotonic())


async def mark_learning_started(product_type: str) -> None:
    """Stamp the record: a learn is in progress."""
    await update_knowledge(product_type, {LEARNING_STARTED_KEY: _now()}, _WRITER)


async def mark_learn_failed(product_type: str) -> None:
    """A failed learn: block a retry for 24 hours and drop the in-progress mark."""
    await update_knowledge(
        product_type, {LEARN_FAILED_KEY: _now()}, _WRITER, drop=(LEARNING_STARTED_KEY,)
    )


async def mark_learning_done(product_type: str) -> None:
    """A finished learn: drop the in-progress mark (the books now make it a profile)."""
    await update_knowledge(product_type, {}, _WRITER, drop=(LEARNING_STARTED_KEY,))


async def auto_learn(product_type: str) -> bool:
    """Learn the category in full: mark started, learn, mark done (the runner of every entry point).

    A failure is marked (no retry for 24 hours), logged with the category, and returned as
    ``False``; it does not propagate, so a background task never dies unseen. A cancel is not a
    failure: the in-progress mark is dropped, so the next search may learn at once.
    """
    try:
        await mark_learning_started(product_type)
        await learn_category(product_type)
        await mark_learning_done(product_type)
    except asyncio.CancelledError:
        await asyncio.shield(mark_learning_done(product_type))
        raise
    except Exception:
        logger.exception("auto-learning of category %r failed", product_type)
        await mark_learn_failed(product_type)
        return False
    return True
