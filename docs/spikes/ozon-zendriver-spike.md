# Ozon.kz: zendriver + curl_cffi instead of Playwright + Chromium

Date: 2026-10-18. Home KZ residential IP. Script: `ozon-zendriver/spike.py`
(`uv run --no-project --with zendriver --with curl_cffi python -I spike.py --headful`).

## Verdict: WORKS, headful only

zendriver drives the **system Chrome** (`/usr/bin/google-chrome`, no bundled Chromium)
through the Variti challenge once. The browser's cookies are then set on a curl_cffi
`AsyncSession(impersonate="chrome")` with navigation headers
(`Sec-Fetch-Mode: navigate`, `Sec-Fetch-Dest: document`). After that, composer-api returns JSON without a browser.

| | Playwright (current) | zendriver + curl_cffi |
|---|---|---|
| Challenge | ~15 s | ~25 s, once |
| composer-api request | browser navigation, 2–4 s | **0.4 s** (first 5.5 s: connection setup) |
| Browser footprint | own Chromium, ~150 MB | system Chrome |
| Headless | passes | **fails**: "Похоже, нет соединения", incident `fab_chlg_…` |

Baseline: current `OzonBrowser` passed on the same IP in the same session (16.9 s, 33 widgets).

## Gotchas

- Cookies must keep their **domain/path** (`s.cookies.set(name, value, domain=, path=)`).
  A plain name→value dict gets a 302 from `www.ozon.kz` to `ozon.kz`.
- Wait for the page title to change from "Antibot Challenge Page" (poll up to 40 s). A fixed 15 s wait is too short in headful mode.

## Not verified

- Cookie lifetime, which sets how often the browser must reopen.
- PDP and reviews pages through curl_cffi (only search was tested).
- Hiding the headful window (off-screen or minimized), and Xvfb for Docker.

## Plan (decided 2026-10-18)

1. **v1:** keep Playwright and launch the system Chrome with `channel="chrome"`, so the desktop build carries no Chromium.
   Before shipping, check that headless system Chrome still passes Variti; this has not been tested yet.
2. **Next:** switch to zendriver + curl_cffi after a second spike that covers cookie lifetime, the PDP and reviews pages, and hiding the window.

## Gate: headless system Chrome through Playwright (2026-10-21)

Task 1.2 of `openspec/changes/ozon-system-chrome`. Home KZ IP, one `OzonSource.search("смартфон")` per run,
`BrowserSession(channel="chrome")` from market-sources `5aca93e`. Google Chrome 155.0.8059.84, Playwright 1.61.0.

| Run | Browser | Saved session | Result |
|---|---|---|---|
| 1 | system Chrome, headless | none | **403**, `OzonSessionExpired` |
| 2 | system Chrome, headless | none | **403**, `OzonSessionExpired` (repeat) |
| 3 | system Chrome, headless | `ozon_state.json` from 2026-10-13 (bundled Chromium, headful setup) | passes, 8 products, 17.1 s |
| 4 | bundled `chrome-headless-shell` v1228 (today's path) | none | passes, 8 products |

**Verdict: gate FAILED for the anonymous case.** Headless Chrome does not pass Variti on its own, while today's
bundled headless shell does. With a session saved by `ozon-setup` it works, and a session saved under Chromium
carries over to Chrome, as the design assumed. Per the design the change stops here and is re-planned.
