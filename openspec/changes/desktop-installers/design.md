# Design

## Context

`desktop.yml` builds a PyInstaller one-dir app per OS, self-tests it and archives it; `release.yml` calls it and
attaches the archives to the release. There is no app icon yet.

## Decisions

- **`.deb` with `dpkg-deb`**, no packaging framework: the one-dir build goes to `/opt/buysmarter`, a
  `buysmarter.desktop` entry to `/usr/share/applications`, an SVG icon to the hicolor theme. `Depends:` lists the Qt
  runtime libraries the offscreen self-test does not need but a real screen does (`libxcb-cursor0`, `libegl1`,
  `libxkbcommon0`, `libfontconfig1`, ...), so `apt install ./buysmarter_<v>_amd64.deb` pulls them in. Built on
  ubuntu-22.04 like the archive. CI installs the `.deb` with `apt` and self-tests the installed binary.
- **Qt desktop file name** `buysmarter`: Wayland docks match the window to `buysmarter.desktop` by it, which gives the
  window its icon.
- **No `/usr/bin` link:** the app starts from the menu; `buysmarter` and `buysmarter-gui` on the PATH belong to the
  pip / script install, and a link would shadow them.
- **Windows: Inno Setup**, per-user install (`PrivilegesRequired=lowest`), preinstalled on the windows runners.
- **macOS: `hdiutil`** creates the `.dmg`, no extra tool.
- **Release notes** from `CHANGELOG.md` (the project already keeps it by hand), by `.github/release-notes.sh`. The
  `check` job fails a tag whose version has no CHANGELOG section, before anything is published.

## Risks / Trade-offs

- [Risk] A dependency name differs between Ubuntu releases → CI installs the `.deb` on the build runner; the docs
  name 22.04 as the minimum.
- [Risk] Unsigned installers still warn (SmartScreen, Gatekeeper) → documented, as for the archives.
