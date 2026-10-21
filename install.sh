#!/bin/sh
# buy-smarter installer for macOS / Linux.
#   curl -LsSf https://raw.githubusercontent.com/octrow/buy-smarter/main/install.sh | sh
#   ... | sh -s -- --version 0.1.0   # a specific release instead of the latest one
#   ... | sh -s -- --main            # current main branch instead of a release
#   ... | sh -s -- --uninstall       # remove the app, keep its data
# Re-running it updates. INSTALL_SOURCE=<path or uv spec> installs from there instead (CI, local testing).
set -eu

REPO="octrow/buy-smarter"
NAME="buysmarter"
CMD="buysmarter"
PYTHON="3.12"   # pinned so binary wheels of the dependencies exist; bump when they ship for newer Pythons
VERSION=""
MAIN=0
UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --version) [ $# -ge 2 ] || { echo "Add a version number: --version 0.1.0" >&2; exit 2; }
               VERSION="${2#v}"; shift ;;
    --main) MAIN=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) echo "options: --version X.Y.Z | --main | --uninstall"; exit 0 ;;
    *) echo "Unknown option: $1. See: --help" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
# curl | sh takes stdin from the pipe, so interactive steps read the terminal directly
has_tty() { (: </dev/tty) 2>/dev/null; }

export PATH="$HOME/.local/bin:$PATH"

if [ "$UNINSTALL" = 1 ]; then
  if have uv; then uv tool uninstall "$NAME" || true; fi
  say "$CMD is removed."
  note "Cache and settings stay in ~/.cache/buysmarter and ~/.config/buysmarter."
  exit 0
fi

# 1. uv: installs Python and the app into an isolated environment, no system Python or sudo needed
if ! have uv; then
  say "Installing uv (it installs Python and $CMD)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  have uv || die "uv is installed, but this terminal does not see it. Open a new terminal and run this command again."
fi

# 2. the app: the release wheel built and tested by CI, or the main branch
if [ -n "${INSTALL_SOURCE:-}" ]; then
  SPEC="$INSTALL_SOURCE"
  [ -d "$SPEC" ] && SPEC="$SPEC[gui]"  # a source checkout: add the desktop app as the release does
elif [ "$MAIN" = 1 ]; then
  SPEC="$NAME[gui] @ https://github.com/$REPO/archive/refs/heads/main.tar.gz"
else
  if [ -z "$VERSION" ]; then
    # /releases/latest redirects to /releases/tag/vX.Y.Z; with no release yet it lands on /releases
    URL="$(curl -fsSLo /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest")" \
      || die "GitHub is not reachable. Check your internet connection and run this command again."
    case "$URL" in */tag/v*) VERSION="${URL##*/tag/v}" ;; *) die "No release yet. Run this command again with --main." ;; esac
  fi
  WHEEL="$(printf %s "$NAME" | tr '-' '_')-$VERSION-py3-none-any.whl"
  SPEC="$NAME[gui] @ https://github.com/$REPO/releases/download/v$VERSION/$WHEEL"
fi
say "Installing $CMD${VERSION:+ $VERSION}"
uv tool install --reinstall --python "$PYTHON" "$SPEC"
uv tool update-shell >/dev/null 2>&1 || true
BIN="$(uv tool dir --bin)/$CMD"

# 3. Ozon needs a browser; optional. Google Chrome needs only `ozon-setup` (doctor says so),
# otherwise ask about Playwright's Chromium (~150 MB)
TOOL_PY="$(uv tool dir)/$NAME/bin/python"
if has_tty && ! "$BIN" doctor 2>/dev/null | grep -Eq '^Ozon browser +(ok|missing +Ozon off until set up)'; then
  printf 'Install the browser for Ozon.kz now (~150 MB)? [y/N] '
  read -r answer </dev/tty || answer=n
  case "$answer" in [yY]*) "$TOOL_PY" -m playwright install chromium || warn "Browser not installed; Ozon stays off." ;; esac
fi

say "Checking the setup"
"$BIN" doctor || warn "Every missing piece only turns off part of the search; see the hints above."

note "Command not found? Open a new terminal or run: $BIN"
note "In the terminal: $CMD recommend \"water flosser, IPX7, under 40000 tg, Almaty\""
say "$CMD is installed. To open the app, run: $CMD gui"
