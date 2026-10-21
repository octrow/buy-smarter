# Qt desktop helpers (PySide6): asyncio ↔ Qt, workers, packaging

repo-scout runs 2026-10-22 (`~/.local/share/repo-scout/runs/20261021-21*`). Our baseline: `src/buysmarter/gui/bridge.py` runs one asyncio loop in a worker thread and returns results as Qt signals; packaging is PyInstaller.

## asyncio ↔ Qt event loop

- **CabbageDevelopment/qasync**: 409★, release v0.28.0 (2025-08), merges up to 2026-07. repo-scout flagged it `dead` because the last *substantive* commit is >6 months old; it's mature, not abandoned.
  `triage: step 1 · close n/a (dropped as dead) · flags: — · verdict: → Study (single Qt-thread loop vs our thread loop; shutdown semantics)`
- **jhnnsrs/koil**: 10★, 168 commits, pushed 14 days ago. Same model as our bridge: a dedicated loop on a background thread, cooperative cancellation, ContextVar propagation, predictable teardown.
  `triage: step 1 · close 0.80 · score 3 (fresh 3, pop 0) · verdict: → Study (loop teardown + cancellation vs gui/bridge.py)`
- Not found by the search, worth knowing: **PySide6.QtAsyncio** ships with PySide6 (since 6.6) and runs asyncio on the Qt loop without a third-party dependency.

## Background workers

- **JoeAnonimist/pyside6-blueprints-code**: book examples, including signals and threading chapters.
  `triage: step 1 · close 0.76 · score 3 · verdict: → Inspect`
- **PerrinYong/pyside6-windows-desktop-infra**: Windows-first baseline covering a background-work layer, pytest-qt, `pyside6-deploy` and GitHub Actions. 1★, history squashed into 1 commit.
  `triage: step 1 · close 0.77 · score 1 · verdict: → Inspect`

## Packaging and release

- **dennisvang/tufup**: TUF-signed auto-updates for stand-alone (PyInstaller) apps. 148★.
  `triage: step 1 · verdict: → Inspect (auto-update)`
- **SHIINASAMA/pyside-template**: PySide6 template with qasync, Nuitka and PyQtDarkTheme, plus a build script. Its README says it's unstable.
  `triage: step 1 · close 0.74 · score 1 · flags: template · verdict: → Inspect`
- **ok-oldking/pyappify**: Rust launcher; on first run it clones git and runs pip install.
  `verdict: doubt (needs network at first run, against our offline/stability priority)`
- **inject3r/pyinstaller-gui**: `drop (GUI wrapper over PyInstaller, 4 commits)`
- Known, not new: pyinstaller, pyinstaller-hooks-contrib.

## Agents and testing

- **com55/pyside6-mcp**: Playwright-style MCP that lets an agent see and drive a PySide6 app. 6★, active.
  `triage: step 1 · close 0.73 · verdict: → Inspect`
