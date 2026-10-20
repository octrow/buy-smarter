# Tasks

## 1. market-sources

- [x] 1.1 Add `channel: str | None = None` to `PlaywrightSession` and pass it to both `chromium.launch` calls; unit test in market-sources asserts the kwarg reaches `launch`
- [ ] 1.2 GATE: live check that headless `channel="chrome"` passes Variti on ozon.kz (one search, at most 3 navigations); record the result in `docs/spikes/ozon-zendriver-spike.md`; stop and re-plan if it fails
  - 2026-10-21: FAILED anonymously (403 twice); passes with a saved session. See the spike doc.
- [ ] 1.3 Release market-sources (minor bump, changelog); verify `uv pip install market-sources==<new>` resolves

## 2. buy-smarter

- [ ] 2.1 Bump `market-sources` and add browser selection (chrome → bundled chromium → none), cached per process; tests with stubbed launch errors cover all three outcomes
- [ ] 2.2 Ozon skipped for lack of a browser surfaces in the report's could-not-verify section; test asserts the note and that Kaspi/OLX results are still returned
- [ ] 2.3 `ozon-setup` uses the selected browser; test asserts the channel passed
- [ ] 2.4 `doctor` "Ozon browser" row (chrome / chromium / none + hint); tests for the three rows
- [ ] 2.5 `install.sh` / `install.ps1` skip the Chromium prompt when Chrome is found; verify with the install CI workflow on a runner with Chrome preinstalled
