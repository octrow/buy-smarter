#!/bin/sh
# Build buysmarter_<version>_<arch>.deb (amd64 or arm64, the build host's) from the PyInstaller one-dir build.
# Usage: packaging/linux/build-deb.sh <version> <one-dir build> <output dir>
# The app goes to /opt/buysmarter, the menu entry and icon to /usr/share; Depends pulls in the Qt runtime
# libraries a real screen needs (the offscreen self-test does not), so `apt install ./<deb>` brings them:
# what `ldd` finds outside the bundle for the X11 and Wayland plugins, plus the xcb/xkb libraries
# PyInstaller bundles only when the build host had them.
set -eu
umask 022
version="$1" build="$2" out="$3"
arch="$(dpkg --print-architecture)"
glibc="$(ldd --version | head -n 1 | awk '{print $NF}')"  # the build host's: the bundle needs at least it
here="$(cd "$(dirname "$0")" && pwd)"
icon="$here/../../src/buysmarter/gui/theme/icon.svg"
root="$(mktemp -d)"
chmod 755 "$root"
trap 'rm -rf "$root"' EXIT

mkdir -p "$root/opt" "$root/usr/share/applications" "$root/usr/share/icons/hicolor/scalable/apps" "$root/DEBIAN"
cp -a "$build" "$root/opt/buysmarter"
chmod -R u=rwX,go=rX "$root/opt/buysmarter"  # whatever umask the build ran under
install -m 644 "$here/buysmarter.desktop" "$root/usr/share/applications/buysmarter.desktop"
install -m 644 "$icon" "$root/usr/share/icons/hicolor/scalable/apps/buysmarter.svg"
cat > "$root/DEBIAN/control" <<CONTROL
Package: buysmarter
Version: $version
Architecture: $arch
Maintainer: Daniil Petrov <octrow@users.noreply.github.com>
Installed-Size: $(du -sk "$root" | cut -f1)
Depends: libc6 (>= $glibc), libegl1, libgl1, libdrm2, libwayland-client0, libwayland-cursor0, libwayland-egl1, libxcb1, libfontconfig1, libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-keysyms1, libxcb-shape0, libdbus-1-3
Suggests: google-chrome-stable
Section: utils
Priority: optional
Homepage: https://github.com/octrow/buy-smarter
Description: Explainable shopping assistant for Kaspi, Ozon and OLX
 Searches Kaspi.kz, Ozon.kz and OLX.kz, ranks the offers on price, reviews, seller
 and specs, and explains the pick. Ozon needs Google Chrome.
CONTROL
mkdir -p "$out"
dpkg-deb --root-owner-group -Zxz --build "$root" "$out/buysmarter_${version}_${arch}.deb"
