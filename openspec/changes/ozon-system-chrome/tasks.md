# Tasks

## 1. market-sources

- [x] 1.1 Add `channel: str | None = None` to `PlaywrightSession` and pass it to both `chromium.launch` calls; unit test in market-sources asserts the kwarg reaches `launch`
- [ ] 1.2 GATE: live check that headless `channel="chrome"` passes Variti on ozon.kz (one search, at most 3 navigations); record the result in `docs/spikes/ozon-zendriver-spike.md`; stop and re-plan if it fails
  - 2026-10-21: FAILED anonymously (403 twice); passes with a saved session. See the spike doc.
- [ ] 1.3 Release market-sources (minor bump, changelog); verify `uv pip install market-sources==<new>` resolves

## 2. buy-smarter

- [ ] 2.1 Bump `market-sources` and add browser selection (chrome with a saved session → bundled chromium → none), launch probe cached per process; tests with stubbed probes cover every outcome
  - 2026-10-21: selection done (`services/ozon_browser.py`); the `market-sources` bump waits for 1.3.
- [x] 2.2 Ozon skipped for lack of a browser surfaces in the report's could-not-verify section; test asserts the note and that Kaspi/OLX results are still returned
- [x] 2.3 `ozon-setup` uses the selected browser; test asserts the channel passed
- [x] 2.4 `doctor` "Ozon browser" row (chrome / chromium / needs setup / none + hint); tests for each row
- [ ] 2.5 `install.sh` / `install.ps1` skip the Chromium prompt when Chrome is found and point to `buysmarter ozon-setup`; verify with the install CI workflow on a runner with Chrome preinstalled
  - 2026-10-21: scripts changed and their pattern checked against real `doctor` output (Chrome and Chromium); the CI run waits for a push.
