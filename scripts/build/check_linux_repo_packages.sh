#!/usr/bin/env bash
# Fail unless a directory holds the packages of exactly one release: each .deb is
# package meshloom at that release's version, each .rpm has that release's name.
# With --keyring, every package must also be signed.
#
# Usage: scripts/build/check_linux_repo_packages.sh --dir DIR --version X.Y.Z|X.Y.Z-rc.N [--keyring FILE]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

die() {
    echo "::error::$*" >&2
    exit 1
}

DIR=""
VERSION=""
KEYRING=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dir) DIR="${2:-}"; shift 2 ;;
        --version) VERSION="${2:-}"; shift 2 ;;
        --keyring) KEYRING="${2:-}"; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
[ -d "$DIR" ] || die "--dir is required"

# What nFPM writes: X.Y.Z-rc.N becomes X.Y.Z~rc.N, and GitHub may turn the ~ of an
# asset name into a dot. The version inside a .deb does not change.
if [[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    DEB_VERSION="$VERSION-1"
    DEB_ARCHES="amd64 arm64 armhf"
    RPM_RE="^meshloom-${VERSION//./\\.}-1\.(x86_64|aarch64)\.rpm$"
elif [[ $VERSION =~ ^([0-9]+\.[0-9]+\.[0-9]+)-rc\.([1-9][0-9]*)$ ]]; then
    DEB_VERSION="${BASH_REMATCH[1]}~rc.${BASH_REMATCH[2]}-1"
    DEB_ARCHES="amd64 arm64"
    RPM_RE="^meshloom-${BASH_REMATCH[1]//./\\.}[~.]rc\.${BASH_REMATCH[2]}-1\.(x86_64|aarch64)\.rpm$"
else
    die "--version must be X.Y.Z or X.Y.Z-rc.N, not '$VERSION'"
fi

shopt -s nullglob
debs=("$DIR"/*.deb)
rpms=("$DIR"/*.rpm)
[ "${#debs[@]}" -gt 0 ] || die "no .deb in $DIR"
[ "${#rpms[@]}" -gt 0 ] || die "no .rpm in $DIR"

for deb in "${debs[@]}"; do
    name="$(basename "$deb")"
    [ "$(dpkg-deb -f "$deb" Package)" = "meshloom" ] || die "$name is not the meshloom package"
    actual="$(dpkg-deb -f "$deb" Version)"
    [ "$actual" = "$DEB_VERSION" ] || die "$name is version $actual, expected $DEB_VERSION for release $VERSION"
    case " $DEB_ARCHES " in
        *" $(dpkg-deb -f "$deb" Architecture) "*) ;;
        *) die "$name: release $VERSION does not serve this architecture" ;;
    esac
done
for rpm in "${rpms[@]}"; do
    [[ "$(basename "$rpm")" =~ $RPM_RE ]] || die "$(basename "$rpm") is not a package of release $VERSION, expected $RPM_RE"
done

if [ -n "$KEYRING" ]; then
    python3 -I "$SCRIPT_DIR/check_deb_signed.py" --keyring "$KEYRING" "${debs[@]}"
    python3 -I "$SCRIPT_DIR/check_rpm_signed.py" "${rpms[@]}"
fi
