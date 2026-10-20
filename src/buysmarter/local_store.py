"""Local store start-up: schema, then the one-time import of pre-store JSON files.

Every in-process entry point (CLI, MCP, GUI) calls ``prepare()`` before its event loop starts.
"""

import asyncio
import logging
from pathlib import Path

from buysmarter.db.schema import ensure_schema

logger = logging.getLogger(__name__)


def set_aside(path: Path, suffix: str) -> None:
    """Rename ``path`` to ``path + suffix``; a failure is logged, not raised (re-import is safe)."""
    try:
        path.rename(path.with_name(path.name + suffix))
    except OSError as exc:
        logger.warning("Could not rename %s to *%s (%r)", path, suffix, exc)


async def _import_legacy() -> None:
    # Imported here: both modules import set_aside from this one.
    from buysmarter.knowledge.store import import_legacy_dir
    from buysmarter.memory.prefs import import_legacy_file

    for step in (import_legacy_file, import_legacy_dir):
        try:
            await step()
        except Exception as exc:  # noqa: BLE001 - the next start retries; only missing keys go in
            logger.warning("Legacy import %s failed (%r); retried next start", step.__name__, exc)


def prepare() -> None:
    """Bring the store to head and import legacy data; a failure leaves the store off, not the app.

    ``doctor`` reports the store as failing in that case (history, preferences, learned categories).
    """
    try:
        ensure_schema()
        asyncio.run(_import_legacy())
    except Exception as exc:  # noqa: BLE001 - the app runs without its store
        logger.warning("Local store unavailable (%r); history and preferences off", exc)
