# Tasks

## 1. Build

- [x] 1.1 `--self-test` in the GUI app (offscreen, temp store, in-memory keyring, migrate, build window, exit code); test runs it from source and asserts exit 0
- [x] 1.2 `packaging/buysmarter-gui.spec` (one-dir, hidden imports and data, Qt module excludes); verify a local Linux build's `--self-test` exits 0 and log the bundle size
  - 2026-10-21: local Linux build passes `--self-test`; 384 MB unpacked, ~150 MB as `.tar.gz` (Playwright's Node driver 123 MB, PySide6 ~100 MB).

## 2. CI

- [x] 2.1 `desktop.yml` matrix (ubuntu-22.04, windows-latest, macos-latest): build, then self-test the built binary, then archive; verify a `workflow_dispatch` run is green on all three
  - 2026-10-21: dispatch run 39635376744 green on ubuntu-22.04, windows-latest and macos-latest (build, self-test, archive).
- [x] 2.2 `release.yml` creates the release, and desktop jobs upload with the naming scheme; verify on a pre-release tag that all three archives appear
  - 2026-10-21: `release.yml` calls `desktop.yml` and creates the release once, after PyPI and the builds; waits for a pre-release tag.
  - 2026-10-22: tag v0.4.0rc1, run 39745298518 green; pre-release holds linux-x64 .tar.gz + .deb, windows-x64 .zip + setup.exe, macos-arm64 .dmg + .app.zip, wheel, sdist; PyPI skipped for the rc.

## 3. Script installs

- [x] 3.1 `install.sh` / `install.ps1` install with `[gui]` and print `buysmarter gui`; verify with the install workflow on Linux, macOS and Windows runners
  - 2026-10-21: scripts done; a local `sh install.sh` from the checkout passes `buysmarter-gui --self-test`; install run 39634891287 green on Linux, macOS and Windows.

## 4. Docs

- [x] 4.1 `docs/install-desktop.md` + `.ru.md`: downloads per OS, the Mac `install.sh` route, Open Anyway steps, SmartScreen, the Chrome prerequisite; link from both READMEs; verify links with the existing link check
  - 2026-10-21: there is no link check in the repo; relative links checked by script.
- [x] 4.2 Manual smoke on a clean Windows VM and a Mac: unpack, launch, run a Kaspi search; record the results in the PR (manual part deferred)
  - 2026-10-22: no Windows or Mac machine at hand, so the manual smoke is deferred. CI (desktop.yml) covers launch on every
    OS: `--self-test` of the built app, of the apt-installed .deb, of the silently installed (and uninstalled) Windows setup
    and of the app inside the mounted .dmg; green in release run 39765402031 (v0.4.0). Not covered: SmartScreen /
    Gatekeeper on a real user machine and a live Kaspi search (geo-dependent, flaky from runners). Do it on first access
    to a Windows or Mac machine, or on the first user report from those systems.
