"""Health checks for every optional piece the pipeline can use; shared by the CLI and the GUI."""

import asyncio
import os
import sys
from pathlib import Path
from typing import NamedTuple

from market_sources.ozon.browser import OzonBrowser, ozon_state_path
from market_sources.ozon.source import search_path
from sqlalchemy.engine import make_url

from buysmarter.config import get_settings
from buysmarter.db.schema import schema_is_current
from buysmarter.llm.adapter import current_route, route_label
from buysmarter.services import ozon_browser
from buysmarter.services.ozon_browser import CHROME, LABELS, channel, search_browser, setup_browser

OZON_BROWSER = "Ozon browser"  # the row whose failure turns Ozon off
_OZON_SETUP_PROBE_QUERY = "смартфон"
INSTALL_BROWSER = (
    "install Google Chrome (https://www.google.com/chrome/), "
    f'or run `"{sys.executable}" -m playwright install chromium`'
)


async def pass_ozon_check() -> bool:
    """Open a visible browser to pass Ozon's anti-bot check once; True when the session is saved."""
    browser = setup_browser()
    if browser is None:
        return False
    settings = get_settings()
    session = OzonBrowser(
        base_url=settings.ozon_base_url, state_path=ozon_state_path(), channel=channel(browser)
    )
    return await session.bootstrap(session.composer_url(search_path(_OZON_SETUP_PROBE_QUERY)))


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
    hint = (
        f"{off}; check DATABASE_URL={location} (unset it for the default SQLite store), "
        "install its driver (`buysmarter[server]`) and run `alembic upgrade head`"
    )
    return Check("Storage", asyncio.run(schema_is_current(url)), hint, location)


def llm_check() -> Check:
    """Where LLM calls go and where the key came from (never the key)."""
    route = current_route(get_settings())
    hint = (
        "LLM parsing and review summaries off; run `buysmarter llm set-key <provider>`, "
        "set an env key such as OPENAI_API_KEY, or install Claude Code and OmniRoute"
    )
    return Check("LLM", route is not None, hint, route_label(route) if route else "")


def ozon_browser_check() -> Check:
    """The browser Ozon will run on, or why Ozon is off."""
    if browser := search_browser():
        return Check(OZON_BROWSER, True, "", LABELS[browser])
    if ozon_browser.launches(CHROME):  # headless Chrome passes Variti only with a saved session
        hint = "Ozon off until set up; run `buysmarter ozon-setup` (opens Google Chrome once)"
        return Check(OZON_BROWSER, False, hint)
    return Check(OZON_BROWSER, False, f"Ozon off; {INSTALL_BROWSER}")


def doctor_checks() -> list[Check]:
    """One row for every optional piece the pipeline can use."""
    return [
        _storage_check(get_settings().database_url),
        llm_check(),
        ozon_browser_check(),
        Check(
            "Ozon session",
            ozon_state_path().exists(),
            "Ozon may hit the anti-bot page; run `buysmarter ozon-setup`",
        ),
    ]
