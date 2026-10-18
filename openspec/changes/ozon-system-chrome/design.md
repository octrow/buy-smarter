# Design

## Context

`market_sources.playwright_session.PlaywrightSession` launches with `self._pw.chromium.launch(headless=…, args=…)` (search) and `driver.chromium.launch(headless=False, …)` (setup). There is no `channel` kwarg. buy-smarter builds `OzonSource(OzonBrowser(base_url=…))` in `services/collect.py`, and `doctor` checks `_chromium_installed()`.

## Goals / Non-Goals

**Goals:** no bundled Chromium needed when Chrome exists; same Variti pass rate as today.
**Non-Goals:** zendriver/curl_cffi (next change); Edge or other Chromium browsers.

## Decisions

- **`channel` lives in market-sources** as `PlaywrightSession(channel: str | None = None)`. The default `None` keeps every other consumer unchanged. Alternative: subclass in buy-smarter. Rejected: the launch call is private to the session.
- **Detecting Chrome.** Use Playwright's own resolution: attempt a launch with `channel="chrome"` and catch the "executable doesn't exist" error once per process. That gives one source of truth and no per-OS path tables. The result is cached for the process.
- **Stealth args unchanged.** The current `LAUNCH_ARGS` apply to both channels.
- **Saved Ozon state.** The Playwright `storage_state` file is browser-agnostic JSON, so a session saved under Chromium keeps working under Chrome.

## Risks / Trade-offs

- [Risk] Headless system Chrome fails Variti, as headless zendriver did → Task 1.2 is a live gate run before any buy-smarter code. If it fails, stop and re-plan (likely option: headful + off-screen window).
- [Risk] Chrome auto-updates past the Playwright-supported version → Playwright drives stable Chrome over CDP, which tolerates this. `doctor` shows the version for bug reports.
