# Tasks

## 1. Foundations

- [x] 1.1 Add the `[gui]` extra (PySide6) and `pytest-qt` to dev; `buysmarter gui` command and `buysmarter-gui` script; verify `buysmarter gui --help` and an offscreen start/exit test
- [x] 1.2 Move `doctor_checks()` to `buysmarter/services/doctor.py`; existing doctor CLI tests stay green
- [x] 1.3 `CoreRunner` bridge (loop thread, submit, finished/failed signals, cancel, shutdown); pytest-qt tests: result arrives on the GUI thread, exception becomes `failed`, cancel stops a sleeping coroutine, shutdown joins the thread
- [x] 1.4 Progress handler + `extra` fields on the stage and per-source log calls; test that a stubbed run emits running → done per source

## 2. Search and Report

- [x] 2.1 Search screen (query, city, budget, marketplace toggles prefilled from prefs, per-source status, cancel); qtbot test drives a stubbed recommend through start → progress → report
- [x] 2.2 Report: best-pick card with per-axis score/max and reason; test against a fixture `RecommendResult`
- [x] 2.3 Alternatives table (sort and filter by axis, price, marketplace via proxy model); test sort order and the marketplace filter
- [x] 2.4 Rejected-with-reasons, OLX used listings, could-not-verify sections; fixture test covers skipped Ozon and skipped LLM steps
- [x] 2.5 Offer activation opens the URL via `QDesktopServices`; test with the open call patched

## 3. Preferences and History

- [x] 3.1 Preferences screen (read/edit/save the supported keys); test that a saved city pre-fills Search after re-creating the window
- [x] 3.2 Price history screen (search term → snapshots newest first) on the SQLite store; test with seeded snapshots
- [x] 3.3 Refresh tracked with progress and cancel; test with a stubbed `refresh_tracked`

## 4. Settings and errors

- [x] 4.1 Settings: doctor rows with hints, store path (the Storage row), "Pass Ozon check" running ozon-setup via the bridge; tests for the rows; Ozon off in Search when its browser is missing
- [x] 4.1b Settings: LLM key set/clear (masked); test that the key is never displayed after save
- [x] 4.2 Error handling: user-facing message, log file traceback, `sys.excepthook` dialog; test that a failing job shows a message and the window stays open

## 5. Look

- [x] 5.1 Design pass with the impeccable skill: palette (light/dark), type scale, spacing, component states; deliver the palette dict and QSS
- [x] 5.2 Theme switching on OS color scheme change and bundled Inter; test that both palettes pass 4.5:1 on every text/background pair
- [x] 5.3 Manual check on Linux in light and dark: screenshots of all five screens, attached to the PR
  - 2026-10-22: no PR (work lands on main) and review screenshots are not tracked (docs/review is ignored). Grabbed all six
    sidebar screens offscreen at 1280×800 in light and dark (temp store, a copy of the saved reports, palette forced
    since offscreen has no OS scheme). Both themes render; no clipped text or overflow. Left for polish: card inner
    padding differs (Search 24px, History/Status 33px, group titles 3px off their content), the report's second table
    is narrower than the first, empty space under «Сохранить» in Настройки поиска.
