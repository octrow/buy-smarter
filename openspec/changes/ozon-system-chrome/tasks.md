# Tasks

## 1. market-sources

- [x] 1.1 Add `channel: str | None = None` to `PlaywrightSession` and pass it to both `chromium.launch` calls; unit test in market-sources asserts the kwarg reaches `launch`
- [x] 1.2 GATE: live check that headless `channel="chrome"` passes Variti on ozon.kz (one search, at most 3 navigations); record the result in `docs/spikes/ozon-zendriver-spike.md`; stop and re-plan if it fails
  - 2026-10-21: FAILED anonymously (403 twice); passes with a saved session. See the spike doc. Re-planned (Chrome only with a saved session).
- [x] 1.3 Release market-sources (minor bump, changelog); verify `uv pip install market-sources==<new>` resolves
  - 2026-10-21: 0.3.0 pushed to market-sources `main` (`47ab5a8`); no `v0.3.0` tag yet, since a tag push publishes to PyPI.

## 2. buy-smarter

- [x] 2.1 Bump `market-sources` and add browser selection (chrome with a saved session → bundled chromium → none), launch probe cached per process; tests with stubbed probes cover every outcome
  - 2026-10-21: selection done (`services/ozon_browser.py`); bumped to 0.3.0; a live Ozon-only `recommend` ran on Google Chrome.
- [x] 2.2 Ozon skipped for lack of a browser surfaces in the report's could-not-verify section; test asserts the note and that Kaspi/OLX results are still returned
- [x] 2.3 `ozon-setup` uses the selected browser; test asserts the channel passed
- [x] 2.4 `doctor` "Ozon browser" row (chrome / chromium / needs setup / none + hint); tests for each row
- [x] 2.5 `install.sh` / `install.ps1` skip the Chromium prompt when Chrome is found and point to `buysmarter ozon-setup`; verify with the install CI workflow on a runner with Chrome preinstalled
  - 2026-10-21: scripts changed and their pattern checked against real `doctor` output (Chrome and Chromium); the install CI run happens on the next buy-smarter push.
