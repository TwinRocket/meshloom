#!/usr/bin/env bash
# Build the apt and dnf trees of the Linux repository, unsigned, from downloaded
# release packages. publish-linux-repo.yml signs and deploys the result.
#
#   apt/dists/stable    pool/main       the last stable release
#   apt/dists/testing   pool/testing    the running release candidate
#   rpm/$basearch                       stable
#   rpm/testing/$basearch               testing
#
# Without --testing-dir, testing serves the stable packages: its apt suite shares
# pool/main, the rpm files are copied (a dnf repository lists files of its own).
#
# Fails closed on an empty stable directory and on a pre-release package in stable.
#
# Usage: scripts/build/build_linux_repo.sh --stable-dir DIR [--testing-dir DIR] --output DIR
set -euo pipefail

# GitHub Pages refuses or throttles sites near 1 GB.
SIZE_WARNING_MB=900

die() {
    echo "::error::$*" >&2
    exit 1
}

STABLE_DIR=""
TESTING_DIR=""
OUTPUT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --stable-dir) STABLE_DIR="${2:-}"; shift 2 ;;
        --testing-dir) TESTING_DIR="${2:-}"; shift 2 ;;
        --output) OUTPUT="${2:-}"; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
[ -d "$STABLE_DIR" ] || die "--stable-dir is required"
[ -n "$OUTPUT" ] || die "--output is required"
if [ -n "$TESTING_DIR" ] && [ ! -d "$TESTING_DIR" ]; then
    die "--testing-dir $TESTING_DIR does not exist"
fi

shopt -s nullglob

stable_debs=("$STABLE_DIR"/*.deb)
stable_rpms=("$STABLE_DIR"/*.rpm)
[ "${#stable_debs[@]}" -gt 0 ] || die "no .deb in $STABLE_DIR"
[ "${#stable_rpms[@]}" -gt 0 ] || die "no .rpm in $STABLE_DIR"

# The last line of defence: whatever chose these packages, stable users never
# get a pre-release. Read from the package for a .deb. A .rpm can only be judged
# by its name here (no rpm tool is needed anywhere else in this job): nFPM writes
# meshloom-X.Y.Z-1.<arch>.rpm for a stable version.
for deb in "${stable_debs[@]}"; do
    version="$(dpkg-deb -f "$deb" Version)"
    case "$version" in
        *~*) die "$(basename "$deb") is a pre-release ($version): refusing to serve it from stable" ;;
    esac
done
for rpm in "${stable_rpms[@]}"; do
    [[ "$(basename "$rpm")" =~ ^meshloom-[0-9]+\.[0-9]+\.[0-9]+-[0-9]+\.[a-z0-9_]+\.rpm$ ]] \
        || die "$(basename "$rpm") looks like a pre-release: refusing to serve it from stable"
done

rpm_arch_dir() {
    case "$1" in
        *aarch64* | *arm64*) echo aarch64 ;;
        *) echo x86_64 ;;
    esac
}

mkdir -p "$OUTPUT/apt/pool/main" "$OUTPUT/rpm/x86_64" "$OUTPUT/rpm/aarch64" \
    "$OUTPUT/rpm/testing/x86_64" "$OUTPUT/rpm/testing/aarch64"
cp "${stable_debs[@]}" "$OUTPUT/apt/pool/main/"
for rpm in "${stable_rpms[@]}"; do
    cp "$rpm" "$OUTPUT/rpm/$(rpm_arch_dir "$rpm")/"
done

TESTING_POOL=pool/main
TESTING_RPMS=("${stable_rpms[@]}")
if [ -n "$TESTING_DIR" ]; then
    testing_debs=("$TESTING_DIR"/*.deb)
    TESTING_RPMS=("$TESTING_DIR"/*.rpm)
    [ "${#testing_debs[@]}" -gt 0 ] || die "no .deb in $TESTING_DIR"
    [ "${#TESTING_RPMS[@]}" -gt 0 ] || die "no .rpm in $TESTING_DIR"
    TESTING_POOL=pool/testing
    mkdir -p "$OUTPUT/apt/pool/testing"
    cp "${testing_debs[@]}" "$OUTPUT/apt/pool/testing/"
fi
for rpm in "${TESTING_RPMS[@]}"; do
    cp "$rpm" "$OUTPUT/rpm/testing/$(rpm_arch_dir "$rpm")/"
done

# One suite, from one pool: dpkg-scanpackages keeps only the highest version it
# sees, so scanning the whole pool would let an rc displace the stable package.
build_apt_suite() {
    local suite="$1" pool="$2" arches arch
    (
        cd "$OUTPUT/apt"
        # Read from the packages rather than listed here. The list was
        # written in three places, and an architecture missing from one of
        # them is a repository that offers itself to a machine it cannot
        # serve: that is what left a 32-bit Raspberry Pi with no candidate.
        arches="$(for d in "$pool"/*.deb; do dpkg-deb -f "$d" Architecture; done | sort -u | xargs)"
        for arch in $arches; do
            mkdir -p "dists/$suite/main/binary-$arch"
            dpkg-scanpackages --arch "$arch" "$pool" /dev/null > "dists/$suite/main/binary-$arch/Packages"
            gzip -k -f "dists/$suite/main/binary-$arch/Packages"
        done
        apt-ftparchive -o APT::FTPArchive::Release::Origin=Meshloom \
            -o APT::FTPArchive::Release::Label=Meshloom \
            -o APT::FTPArchive::Release::Suite="$suite" \
            -o APT::FTPArchive::Release::Codename="$suite" \
            -o APT::FTPArchive::Release::Architectures="$arches" \
            -o APT::FTPArchive::Release::Components=main \
            release "dists/$suite" > "dists/$suite/Release"
    )
}

build_apt_suite stable pool/main
build_apt_suite testing "$TESTING_POOL"

for dir in rpm/x86_64 rpm/aarch64 rpm/testing/x86_64 rpm/testing/aarch64; do
    createrepo_c "$OUTPUT/$dir"
done

for f in apt/dists/stable/Release apt/dists/testing/Release \
    rpm/x86_64/repodata/repomd.xml rpm/aarch64/repodata/repomd.xml \
    rpm/testing/x86_64/repodata/repomd.xml rpm/testing/aarch64/repodata/repomd.xml; do
    [ -f "$OUTPUT/$f" ] || die "$f was not generated"
done

SIZE_MB="$(du -sm "$OUTPUT" | awk '{print $1}')"
echo "Repository size: ${SIZE_MB} MiB"
if [ "$SIZE_MB" -gt "$SIZE_WARNING_MB" ]; then
    echo "::warning::The repository is ${SIZE_MB} MiB, close to the 1 GB limit of GitHub Pages."
fi
