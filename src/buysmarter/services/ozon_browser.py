"""Which browser Ozon runs on: system Chrome once a session is saved, else Playwright's Chromium.

Headless Chrome passes Ozon's anti-bot check only with a session saved by ``ozon-setup`` (gate run
of 2026-10-21, docs/spikes/ozon-zendriver-spike.md); the bundled headless shell passes without one.
"""

import functools
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from market_sources.ozon.browser import ozon_state_path

CHROME = "chrome"  # also the Playwright channel of the installed Google Chrome
CHROMIUM = "chromium"
LABELS = {CHROME: "Google Chrome", CHROMIUM: "Playwright Chromium"}


def channel(browser: str | None) -> str | None:
    """The Playwright launch channel for ``browser``; None is the bundled Chromium."""
    return CHROME if browser == CHROME else None


def _probe(launch_channel: str | None) -> bool:
    try:
        import playwright
        from playwright.sync_api import sync_playwright
    except ImportError:  # installed without the browser extra
        return False

    # A headless launch, not a path check: Playwright resolves Chrome per OS, and the container
    # ships only the headless shell. The slim image has no driver; launching without one logs a
    # traceback.
    if not (Path(playwright.__file__).parent / "driver").is_dir():
        return False
    try:
        with sync_playwright() as p:
            p.chromium.launch(channel=launch_channel).close()
            return True
    except Exception:
        return False


@functools.cache
def launches(browser: str) -> bool:
    """Whether ``browser`` starts; probed once per process, since a launch takes about a second."""
    # Playwright's sync API refuses a thread that runs an asyncio loop: probe on a fresh one.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_probe, channel(browser)).result()


def search_browser() -> str | None:
    """The browser Ozon searches use, or None when Ozon is off."""
    if ozon_state_path().exists() and launches(CHROME):
        return CHROME
    return CHROMIUM if launches(CHROMIUM) else None


def setup_browser() -> str | None:
    """The browser ``ozon-setup`` opens: Chrome when installed, so later searches can use it."""
    return next((browser for browser in (CHROME, CHROMIUM) if launches(browser)), None)
