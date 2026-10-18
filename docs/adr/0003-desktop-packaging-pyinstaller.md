# Desktop builds: PyInstaller one-dir on a GitHub Actions matrix (Linux, Windows, macOS)

PyInstaller has built-in PySide6 hooks and handles native libraries (curl_cffi, keyring backends) with the least risk on our dependency set. Builds run on free GitHub Actions runners (the repo is public). Native installers (Inno Setup on Windows, AppImage on Linux) come as a later step on top of the one-dir output.

**macOS without the $99/yr Apple Developer Program:** the primary path is `install.sh` (`uv tool install`), then `buysmarter gui`. uv/pip installs carry no quarantine flag, so Gatekeeper does not block it. An unsigned `.app` from CI is the secondary path. On Sequoia and later it opens only through System Settings → Privacy & Security → Open Anyway (the Control-click bypass was removed). The official Homebrew tap does not take unsigned casks: they were disabled from 2026-09-01, so the only option is a custom tap.

## Considered Options (revisit later)

- **`pyside6-deploy` (Nuitka)**: a faster and smaller binary, but long compiles and more breakage on dynamic imports (pydantic, fastmcp, keyring plugins). Try it once the PyInstaller build is stable and size or startup time matters.
- **Briefcase (BeeWare)**: produces native installers directly (MSI, DMG, AppImage), but it is less commonly paired with PySide6. Revisit if the hand-made installer step becomes a burden.
- **Signed and notarized `.app`**: needs Apple Developer membership. Add it when macOS users appear.
