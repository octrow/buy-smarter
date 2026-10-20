"""Health checks for every optional piece the pipeline can use; shared by the CLI and the GUI."""

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import NamedTuple

from market_sources.ozon.browser import OzonBrowser, ozon_state_path
from market_sources.ozon.source import search_path
from sqlalchemy.engine import make_url

from buysmarter.config import get_settings
from buysmarter.db.schema import schema_is_current

OZON_BROWSER = "Playwright Chromium"  # the row whose failure turns Ozon off
_OZON_SETUP_PROBE_QUERY = "смартфон"


async def pass_ozon_check() -> bool:
    """Open a visible browser to pass Ozon's anti-bot check once; True when the session is saved."""
    browser = OzonBrowser(base_url=get_settings().ozon_base_url, state_path=ozon_state_path())
    return await browser.bootstrap(browser.composer_url(search_path(_OZON_SETUP_PROBE_QUERY)))


def _chromium_installed() -> bool:
    import playwright
    from playwright.sync_api import sync_playwright

    # A headless launch, not a path check: the container ships only the headless shell.
    # The slim image has no Playwright driver at all, and launching without it logs a traceback.
    if not (Path(playwright.__file__).parent / "driver").is_dir():
        return False
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
            return True
    except Exception:
        return False


class Check(NamedTuple):
    """One ``doctor`` row: ``hint`` says what to do when it fails, ``detail`` is shown when ok."""

    name: str
    ok: bool
    hint: str
    detail: str = ""


def _storage_check(url: str) -> Check:
    """Which store is in use and where; failing when it cannot be opened (or, SQLite, written)."""
    parsed = make_url(url)
    off = "history, preferences and learned categories off"
    if parsed.get_backend_name() == "sqlite":
        path = Path(parsed.database or "")
        target = path if path.exists() else path.parent
        ok = os.access(target, os.W_OK) and asyncio.run(schema_is_current(url))
        hint = f"{off}; {path} is not writable or its schema is not current (see the log)"
        return Check("Storage", ok, hint, f"SQLite {path}")
    location = parsed.render_as_string(hide_password=True)
    hint = f"{off}; check DATABASE_URL and run `alembic upgrade head`"
    return Check("Storage", asyncio.run(schema_is_current(url)), hint, location)


def doctor_checks() -> list[Check]:
    """One row for every optional piece the pipeline can use."""
    return [
        _storage_check(get_settings().database_url),
        Check(
            "claude CLI",
            shutil.which("claude") is not None,
            "LLM parsing and review summaries off; install Claude Code or use --no-llm",
        ),
        Check(
            OZON_BROWSER,
            _chromium_installed(),
            f'Ozon off; run `"{sys.executable}" -m playwright install chromium`',
        ),
        Check(
            "Ozon session",
            ozon_state_path().exists(),
            "Ozon may hit the anti-bot page; run `buysmarter ozon-setup`",
        ),
    ]
