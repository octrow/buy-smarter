# Tasks

## 1. Ubuntu / Debian

- [x] 1.1 App icon (`src/buysmarter/gui/theme/icon.svg`) and the Qt desktop file name `buysmarter`; test asserts the name
- [x] 1.2 `packaging/linux/build-deb.sh` + `buysmarter.desktop`: `/opt/buysmarter`, menu entry, icon, `Depends:`; verify locally that the package content is right, the entry passes `desktop-file-validate`, and the unpacked binary passes `--self-test`
- [x] 1.3 `desktop.yml` builds the `.deb` on ubuntu-22.04, installs it with `apt` and self-tests the installed app; the release attaches it; verify with a `workflow_dispatch` run
- [x] 1.4 Release notes from the CHANGELOG section plus a downloads table; the `check` job fails a tag without a section
- [x] 1.5 Docs: the `.deb` is the recommended Ubuntu route

## 2. Windows

- [x] 2.1 Inno Setup script: per-user install, Start-menu entry, uninstaller; CI installs it silently and self-tests the installed app

## 3. macOS

- [x] 3.1 `.dmg` with the `.app` and an Applications link; CI mounts it and self-tests the app

## 4. Architectures

- [x] 4.1 macOS x64 and Linux arm64 (Ubuntu 24.04: PySide6 aarch64 wheels need glibc 2.39) in the matrix; archive, artifact and `.deb` names carry the arch. Windows arm64 runs the x64 setup emulated: `cryptography` ships no win_arm64 wheel (verified by run 40951429720)
