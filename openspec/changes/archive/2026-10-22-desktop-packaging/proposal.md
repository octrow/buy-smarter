# Proposal

## Why

The desktop app (desktop-gui-v1) needs to reach people who do not have Python. ADR 0003 picks PyInstaller one-dir builds on a free GitHub Actions matrix for Linux, Windows and macOS. On macOS there is no paid Apple Developer membership: the primary path there is `install.sh`, and an unsigned `.app` is offered as a second option.

## What Changes

- A PyInstaller spec that builds the GUI as a one-dir app. It includes the hidden imports and data that the dependencies need: PySide6 plugins, keyring backends, curl_cffi native libs, alembic migrations, the bundled font and QSS.
- A `--self-test` flag on the built app: it starts offscreen, opens the store, runs the migrations, sets and reads a dummy key in an in-memory keyring, and exits 0.
- A `desktop.yml` workflow: a matrix (ubuntu, windows, macos) that `release.yml` calls on `v*` tags, also runnable by hand. It builds, self-tests and archives each build (`.tar.gz`, `.zip`, `.app.zip`); `release.yml` attaches them to the GitHub Release it creates.
- `install.sh` / `install.ps1` install with the `[gui]` extra and print how to launch `buysmarter gui`.
- Install docs (EN/RU): what to download per OS, the macOS "Open Anyway" steps for the unsigned `.app`, the `install.sh` route as the recommended one on Mac, and that Ozon needs Google Chrome.

## Capabilities

### New Capabilities

- `desktop-distribution`: how a non-developer obtains and starts the desktop app on each OS, and what each release must contain.

### Modified Capabilities

(none)

## Impact

- Depends on: `desktop-gui-v1`, plus `ozon-system-chrome` (no Chromium in the bundle) and `llm-key-keyring` (keyring backends in the bundle).
- New files: `packaging/buysmarter-gui.spec`, `.github/workflows/desktop.yml`, `docs/install-desktop.md`, and `docs/install-desktop.ru.md`.
- Changes: `install.sh`, `install.ps1`, `README*.md` (download section), `release.yml` (call the desktop builds and attach them to the release).
- Deferred alternatives (ADR 0003): `pyside6-deploy`/Nuitka, Briefcase, and native installers (Inno Setup, AppImage) as a later step.
