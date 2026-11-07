#!/usr/bin/env bash
# Print the assets of a GitHub release, one path per line, and fail unless a
# directory holds exactly those. Jobs upstream run third-party code: whatever else
# their artifacts carry must not become a release asset.
#
# Usage: scripts/build/list_release_assets.sh --version X.Y.Z|X.Y.Z-rc.N --dir DIR
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/build/release_common.sh
source "$SCRIPT_DIR/release_common.sh"

VERSION=""
DIR=""
while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="${2:-}"; shift 2 ;;
        --dir) DIR="${2:-}"; shift 2 ;;
        *) release_die "Unknown argument: $1" ;;
    esac
done
release_validate_version "$VERSION"
[ -d "$DIR" ] || release_die "--dir is required"

PACKAGE_VERSION="$VERSION"
if [[ $VERSION =~ ^(.+)-rc\.(.+)$ ]]; then
    PACKAGE_VERSION="${BASH_REMATCH[1]}~rc.${BASH_REMATCH[2]}"
fi
ZIP_RE="^meshloom-prebuilt-frontend-v${VERSION//./\\.}-[0-9a-f]+\.zip$"
EXPECTED=(
    "install.sh"
    "meshloom_${PACKAGE_VERSION}-1_amd64.deb"
    "meshloom_${PACKAGE_VERSION}-1_arm64.deb"
    "meshloom-${PACKAGE_VERSION}-1.x86_64.rpm"
    "meshloom-${PACKAGE_VERSION}-1.aarch64.rpm"
)

for name in "${EXPECTED[@]}"; do
    [ -f "$DIR/$name" ] || release_die "$name is missing from $DIR"
done

ZIPS=0
ASSETS=()
while IFS= read -r -d '' file; do
    name="${file#"$DIR"/}"
    [ ! -L "$file" ] || release_die "$name is a symlink"
    if [[ $name =~ $ZIP_RE ]]; then
        ZIPS=$((ZIPS + 1))
    else
        known=0
        for expected in "${EXPECTED[@]}"; do
            [ "$name" != "$expected" ] || known=1
        done
        [ "$known" -eq 1 ] || release_die "$name is not a release asset of $VERSION"
    fi
    ASSETS+=("$file")
done < <(find "$DIR" -mindepth 1 \( -type f -o -type l -o -type d \) -print0)
[ "$ZIPS" -eq 1 ] || release_die "expected one prebuilt frontend zip in $DIR, found $ZIPS"

printf '%s\n' "${ASSETS[@]}" | LC_ALL=C sort
