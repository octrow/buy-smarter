# Proposal

## Why

The desktop builds (desktop-packaging) are archives: the user unpacks a folder and finds the binary in it. Nothing
lands in the app menu, and on Linux the Qt runtime libraries are the user's problem. A non-developer expects the
installer their system uses. Ubuntu comes first: it is the maintainer's own system and the most common desktop
Linux here.

## What Changes

- **Ubuntu / Debian:** a `.deb` built from the one-dir build. It installs to `/opt/buysmarter`, adds an app-menu entry
  with an icon, and declares the Qt runtime libraries as dependencies, so `apt` installs them.
- **Windows:** a setup `.exe` (Inno Setup) with a Start-menu entry and an uninstaller, per user, no admin rights.
- **macOS:** a `.dmg` with the `.app` and an Applications link.
- **More architectures:** macOS x64, Linux arm64 and Windows arm64 builds, on free GitHub runners.
- The release notes come from the version's CHANGELOG.md section plus a downloads table, instead of a bare
  "Full Changelog" link.

## Capabilities

### New Capabilities

- `desktop-installers`: the native installer each OS gets, what it puts where, and how a release lists them.

### Modified Capabilities

(none)

## Impact

- New: `packaging/linux/` (`build-deb.sh`, `buysmarter.desktop`), `src/buysmarter/gui/theme/icon.svg`,
  `packaging/windows/buysmarter.iss`, `.github/release-notes.sh`.
- Changes: `desktop.yml` (installer steps, matrix), `release.yml` (notes), `gui/app.py` (desktop file name, so the
  dock shows the icon), `docs/install-desktop*.md`.
- Still no signing or notarization, no auto-update (desktop-packaging non-goals stand).
