"""When a request may start learning its category on its own (phase 3, decision 8).

Only the condition and the marks live here; the entry points (GUI, MCP, worker, CLI) decide
where to run it and start the learning. The search never waits for the result.
"""

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


def _over_limit() -> bool:
    now = time.monotonic()
    while _starts and now - _starts[0] >= _WINDOW_S:
        _starts.popleft()
    return len(_starts) >= get_settings().auto_learn_per_hour


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
    ``mark_learning_started`` when the learning really starts.
    """
    if not knowledge_missing or intent not in _INTENTS or not use_llm:
        return False
    if not _valid_name(product_type):
        return False
    if not claude_available() or _over_limit():
        return False
    raw = await load_knowledge(product_type) or {}
    if has_books(raw):
        return False
    return not (
        _younger_than(raw, LEARNING_STARTED_KEY, _LEARNING_TTL)
        or _younger_than(raw, LEARN_FAILED_KEY, _FAILED_TTL)
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def mark_learning_started(product_type: str) -> None:
    """Stamp the record and count the start against the hourly limit."""
    _starts.append(time.monotonic())
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
    ``False``; it does not propagate, so a background task never dies unseen.
    """
    try:
        await mark_learning_started(product_type)
        await learn_category(product_type)
        await mark_learning_done(product_type)
    except Exception:
        logger.exception("auto-learning of category %r failed", product_type)
        await mark_learn_failed(product_type)
        return False
    return True
