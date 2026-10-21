# Tasks

## 1. Build

- [x] 1.1 `--self-test` in the GUI app (offscreen, temp store, in-memory keyring, migrate, build window, exit code); test runs it from source and asserts exit 0
- [x] 1.2 `packaging/buysmarter-gui.spec` (one-dir, hidden imports and data, Qt module excludes); verify a local Linux build's `--self-test` exits 0 and log the bundle size
  - 2026-10-21: local Linux build passes `--self-test`; 384 MB unpacked, ~150 MB as `.tar.gz` (Playwright's Node driver 123 MB, PySide6 ~100 MB).

## 2. CI

- [ ] 2.1 `desktop.yml` matrix (ubuntu-22.04, windows-latest, macos-latest): build, then self-test the built binary, then archive; verify a `workflow_dispatch` run is green on all three
- [ ] 2.2 `release.yml` creates the release, and desktop jobs upload with the naming scheme; verify on a pre-release tag that all three archives appear

## 3. Script installs

- [ ] 3.1 `install.sh` / `install.ps1` install with `[gui]` and print `buysmarter gui`; verify with the install workflow on Linux, macOS and Windows runners

## 4. Docs

- [ ] 4.1 `docs/install-desktop.md` + `.ru.md`: downloads per OS, the Mac `install.sh` route, Open Anyway steps, SmartScreen, the Chrome prerequisite; link from both READMEs; verify links with the existing link check
- [ ] 4.2 Manual smoke on a clean Windows VM and a Mac: unpack, launch, run a Kaspi search; record the results in the PR
