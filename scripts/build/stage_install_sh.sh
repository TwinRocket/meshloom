#!/usr/bin/env bash
# The install.sh attached to a release.
#
#   X.Y.Z        scripts/setup/install.sh, unchanged.
#   X.Y.Z-rc.N   the same file with the one line that names this rc, so that it moves a
#                machine there when `get.meshloom.app/next` redirects to this asset.
#                SHA256SUMS is computed afterwards from the asset itself.
#
# Usage: scripts/build/stage_install_sh.sh --version V --output FILE
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="${MESHLOOM_INSTALL_SH:-$SCRIPT_DIR/../setup/install.sh}"
LINE='INSTALLER_PRERELEASE=""'

die() {
    echo "::error::$*" >&2
    exit 1
}

VERSION=""
OUTPUT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="${2:-}"; shift 2 ;;
        --output) OUTPUT="${2:-}"; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
[ -n "$OUTPUT" ] || die "--output is required"

if printf '%s\n' "$VERSION" | grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'; then
    cp "$SOURCE" "$OUTPUT"
    exit 0
fi
printf '%s\n' "$VERSION" | grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc\.[1-9][0-9]*$' ||
    die "'$VERSION' is not X.Y.Z or X.Y.Z-rc.N"
[ "$(grep -cx "$LINE" "$SOURCE")" = 1 ] || die "expected exactly one '$LINE' line in $SOURCE"
trap 'rm -f "$OUTPUT.tmp"' EXIT
sed "s/^${LINE}\$/INSTALLER_PRERELEASE=\"${VERSION}\"/" "$SOURCE" >"$OUTPUT.tmp"
grep -qx "INSTALLER_PRERELEASE=\"${VERSION}\"" "$OUTPUT.tmp" || die "could not stamp the version"
mv "$OUTPUT.tmp" "$OUTPUT"
