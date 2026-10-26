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
STORAGE = "Storage"  # the row whose failure turns history and preferences off
_OZON_SETUP_PROBE_QUERY = "смартфон"
INSTALL_BROWSER = (
    "install Google Chrome (https://www.google.com/chrome/), "
    f'or run `"{sys.executable}" -m playwright install chromium`'
)


# How the Ozon check ended (``pass_ozon_check``), said in the same words by ``ozon-setup`` and the
# app's «Пройти проверку Ozon» (``ozon_setup_message``).
OZON_SAVED = "saved"
OZON_NO_BROWSER = "no_browser"
OZON_TIMEOUT = "timeout"


async def pass_ozon_check() -> str:
    """Open a visible browser to pass Ozon's anti-bot check once; one of the ``OZON_*`` outcomes."""
    browser = setup_browser()
    if browser is None:
        return OZON_NO_BROWSER
    settings = get_settings()
    session = OzonBrowser(
        base_url=settings.ozon_base_url, state_path=ozon_state_path(), channel=channel(browser)
    )
    url = session.composer_url(search_path(_OZON_SETUP_PROBE_QUERY))
    return OZON_SAVED if await session.bootstrap(url) else OZON_TIMEOUT


def ozon_setup_message(outcome: str) -> str:
    """The Ozon check's outcome for the user: the session file, or what to do."""
    if outcome == OZON_SAVED:
        return f"Сессия Ozon сохранена: {ozon_state_path()}"
    if outcome == OZON_NO_BROWSER:
        return (
            "Нет браузера для Ozon: установите Google Chrome (https://www.google.com/chrome/) "
            "и проверьте снова."
        )
    return "Не удалось получить сессию Ozon за отведённое время."


class Check(NamedTuple):
    """One ``doctor`` row: ``hint`` says what to do when it fails, ``detail`` is shown when ok.

    ``hint`` is for the terminal; ``app_hint`` says the same in Russian for the desktop app, with
    what to click there instead of a command to type.
    """

    name: str
    ok: bool
    hint: str
    detail: str = ""
    app_hint: str = ""


def _storage_check(url: str) -> Check:
    """Which store is in use and where; failing when it cannot be opened (or, SQLite, written)."""
    parsed = make_url(url)
    off = "history, preferences and learned categories off"
    app_off = "История цен, настройки поиска и выученные категории выключены"
    if parsed.get_backend_name() == "sqlite":
        path = Path(parsed.database or "")
        target = path if path.exists() else path.parent
        ok = os.access(target, os.W_OK) and asyncio.run(schema_is_current(url))
        hint = f"{off}; {path} is not writable or its schema is not current (see the log)"
        app_hint = f"{app_off}: не удалось открыть файл {path} (подробности в логе)."
        return Check(STORAGE, ok, hint, f"SQLite {path}", app_hint)
    location = parsed.render_as_string(hide_password=True)
    hint = (
        f"{off}; check DATABASE_URL={location} (unset it for the default SQLite store), "
        "install its driver (`buysmarter[server]`) and run `alembic upgrade head`"
    )
    app_hint = (
        f"{app_off}: в ~/.config/buysmarter/.env задан DATABASE_URL на сервер базы данных. "
        "Уберите эту строку, и данные будут храниться на этом компьютере."
    )
    return Check(STORAGE, asyncio.run(schema_is_current(url)), hint, location, app_hint)


def llm_check() -> Check:
    """Where LLM calls go and where the key came from (never the key)."""
    route = current_route(get_settings())
    hint = (
        "LLM parsing and review summaries off; run `buysmarter llm set-key <provider>`, "
        "set an env key such as OPENAI_API_KEY, or install Claude Code and OmniRoute"
    )
    app_hint = (
        "Без AI запрос разбирается по ключевым словам, без сводки отзывов. "
        "Добавьте ключ в блоке «AI-ключ» ниже."
    )
    detail = route_label(route) if route else ""
    return Check("LLM", route is not None, hint, detail, app_hint)


def ozon_browser_check() -> Check:
    """The browser Ozon will run on, or why Ozon is off."""
    if browser := search_browser():
        return Check(OZON_BROWSER, True, "", LABELS[browser])
    if ozon_browser.launches(CHROME):  # headless Chrome passes Variti only with a saved session
        hint = "Ozon off until set up; run `buysmarter ozon-setup` (opens Google Chrome once)"
        app_hint = "Ozon выключен до первой проверки: нажмите «Пройти проверку Ozon» ниже."
        return Check(OZON_BROWSER, False, hint, app_hint=app_hint)
    app_hint = "Ozon выключен: установите Google Chrome (google.com/chrome) и проверьте снова."
    return Check(OZON_BROWSER, False, f"Ozon off; {INSTALL_BROWSER}", app_hint=app_hint)


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
            app_hint="Ozon может показать антибот-страницу: нажмите «Пройти проверку Ozon» ниже.",
        ),
    ]
