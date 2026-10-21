#!/bin/sh
# Release notes for tag $1 (vX.Y.Z): its CHANGELOG.md section plus the downloads table.
# Fails when CHANGELOG.md has no section for the version, so release.yml stops before PyPI.
set -eu
version="${1#v}"
section="$(awk -v v="$version" '
  index($0, "## [" v "]") == 1 { found = 1; next }
  found && /^## \[/ { exit }
  found { print }
' CHANGELOG.md | sed -e '/./,$!d')"
if [ -z "$section" ]; then
  echo "CHANGELOG.md has no '## [$version]' section" >&2
  exit 1
fi
printf '%s\n' "$section"
cat <<NOTES

## Downloads

| System | File |
|---|---|
| Ubuntu / Debian | \`buysmarter_${version}_amd64.deb\`: open it, or \`sudo apt install ./buysmarter_${version}_amd64.deb\` |
| Windows | \`buysmarter-${version}-windows-x64.zip\` |
| macOS (Apple silicon) | \`buysmarter-${version}-macos-arm64.app.zip\` |
| Other Linux | \`buysmarter-${version}-linux-x64.tar.gz\` |

The builds are unsigned: see [Installing the desktop app](https://github.com/octrow/buy-smarter/blob/v${version}/docs/install-desktop.md)
for the first-launch steps. Ozon needs Google Chrome. The command-line tool and MCP server: \`pip install buysmarter==${version}\`
or the install scripts.
NOTES
