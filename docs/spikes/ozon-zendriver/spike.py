"""Spike: zendriver passes Variti once, curl_cffi reuses cookies for composer-api."""

import asyncio
import json
import sys
import time
from urllib.parse import quote

import zendriver as zd
from curl_cffi.requests import AsyncSession

BASE = "https://www.ozon.kz"
QUERIES = ["ирригатор", "адапален"]
HEADLESS = "--headful" not in sys.argv


def composer(path: str) -> str:
    return f"{BASE}/api/composer-api.bx/page/json/v2?url=" + quote(path, safe="")


def summarize(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return f"NOT JSON: {body[:120]!r}"
    widgets = data.get("widgetStates", {})
    tiles = [k for k in widgets if k.startswith("tileGridDesktop")]
    return f"json ok, widgets={len(widgets)}, tileGrid={bool(tiles)}"


CHALLENGE_TIMEOUT_S = 40
NAV_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


async def pass_challenge() -> tuple[list, str]:
    """Pass Variti in system Chrome; return its cookies and user agent."""
    t0 = time.monotonic()
    browser = await zd.start(headless=HEADLESS, browser_executable_path="/usr/bin/google-chrome")
    tab = await browser.get(f"{BASE}/")
    title = ""
    for _ in range(CHALLENGE_TIMEOUT_S):
        await tab.sleep(1)
        title = await tab.evaluate("document.title")
        if "ozon" in title.lower():
            break
    print(f"[zendriver] headless={HEADLESS} challenge {time.monotonic() - t0:.1f}s title={title!r}")
    ua = await tab.evaluate("navigator.userAgent")
    cookies = await browser.cookies.get_all()
    print(f"[zendriver] cookies: {sorted(c.name for c in cookies)}")
    await browser.stop()
    return cookies, ua


async def fetch_via_curl(cookies: list, ua: str) -> None:
    """Hit composer-api search with curl_cffi, reusing the browser's cookies."""
    headers = {**NAV_HEADERS, "User-Agent": ua.replace("HeadlessChrome", "Chrome")}
    async with AsyncSession(impersonate="chrome", headers=headers) as s:
        for c in cookies:
            s.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
        for q in QUERIES:
            t0 = time.monotonic()
            r = await s.get(composer(f"/search/?text={q}"))
            print(
                f"[curl_cffi] {q}: HTTP {r.status_code} {time.monotonic() - t0:.2f}s"
                f" -> {summarize(r.text)}"
            )


async def main() -> None:
    cookies, ua = await pass_challenge()
    await fetch_via_curl(cookies, ua)


asyncio.run(main())
