# Proposal

## Why

The desktop build (ADR 0003) should not carry Playwright's own ~150 MB Chromium. The only reason it ships today is Ozon. Launching the system Chrome through Playwright (`channel="chrome"`) removes it from the bundle with a one-line launch change and keeps the proven Playwright transport (spike: `docs/spikes/ozon-zendriver-spike.md`, Plan step 1). zendriver + curl_cffi comes later, as a separate change.

## What Changes

- `market-sources` (`~/dev/pylibs/market-sources`): `PlaywrightSession` accepts an optional `channel` that is passed to `chromium.launch`, for the search session and the visible `ozon-setup` session alike. Released as a new minor version.
- buy-smarter picks the browser in this order: system Chrome, then Playwright's bundled Chromium, then Ozon off. It records which one it used.
- `doctor` reports the Ozon browser (system Chrome, bundled Chromium, or none) with a hint for each case.
- `install.sh` / `install.ps1` stop offering the Chromium download when system Chrome is present.

## Capabilities

### New Capabilities

- `ozon-source`: how buy-smarter obtains a browser for Ozon.kz, and what the user sees when none is available.

### Modified Capabilities

(none)

## Impact

- Cross-repo: `market-sources` gets a release, and buy-smarter's `pyproject.toml` bumps `market-sources[browser,…]`.
- Code: `services/collect.py` (OzonBrowser construction), `cli/main.py` (`ozon_setup`, `doctor`), `install.sh`, `install.ps1`.
- Gate: if headless system Chrome does not pass Variti, the change stops at task 1.2 and is re-planned.
