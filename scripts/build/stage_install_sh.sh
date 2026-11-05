#!/usr/bin/env bash
# The install.sh attached to a release.
#
#   X.Y.Z        scripts/setup/install.sh, unchanged.
#   X.Y.Z-rc.N   the same file with the one line that makes the pre-release channel
#                its default, so that `get.meshloom.app/next`, which redirects to this
#                asset and cannot pass an argument through `bash -c "$(curl ...)"`,
#                selects it. SHA256SUMS is computed afterwards from the asset itself.
#
# Usage: scripts/build/stage_install_sh.sh --version V --output FILE
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="${MESHLOOM_INSTALL_SH:-$SCRIPT_DIR/../setup/install.sh}"
LINE='INSTALLER_DEFAULT_CHANNEL=stable'

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
sed "s/^${LINE}\$/INSTALLER_DEFAULT_CHANNEL=testing/" "$SOURCE" >"$OUTPUT"
grep -qx 'INSTALLER_DEFAULT_CHANNEL=testing' "$OUTPUT" || die "could not select the channel"
