#!/usr/bin/env bash
# Download and check the packages each suite of the Linux repository will serve:
# <incoming>/stable always, <incoming>/testing when an rc is served.
#
# Stable fails closed. An rc that cannot be served (a download error, a wrong or
# unsigned package, a site too large for GitHub Pages) fails the run only when this
# run publishes that rc. Otherwise <incoming>/testing is left out, with a warning,
# and testing serves stable: a broken candidate never stops a stable publication.
#
# Usage: scripts/build/fetch_linux_repo_packages.sh --stable X.Y.Z --testing TAG
#   --requested TAG --incoming DIR --keyring FILE [--require-armhf] [--size-limit-mb N]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# GitHub Pages: "published sites may be no larger than 1 GB". 50 MiB is left for the
# metadata and the keys, and for what a measure in MiB rounds away.
SIZE_LIMIT_MB=950

die() {
    echo "::error::$*" >&2
    exit 1
}

STABLE=""
TESTING=""
REQUESTED=""
INCOMING=""
KEYRING=""
REQUIRE_ARMHF=0
while [ $# -gt 0 ]; do
    case "$1" in
        --stable) STABLE="${2:-}"; shift 2 ;;
        --testing) TESTING="${2:-}"; shift 2 ;;
        --requested) REQUESTED="${2:-}"; shift 2 ;;
        --incoming) INCOMING="${2:-}"; shift 2 ;;
        --keyring) KEYRING="${2:-}"; shift 2 ;;
        --require-armhf) REQUIRE_ARMHF=1; shift ;;
        --size-limit-mb) SIZE_LIMIT_MB="${2:-}"; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
[ -n "$STABLE" ] && [ -n "$TESTING" ] && [ -n "$INCOMING" ] && [ -n "$KEYRING" ] \
    || die "--stable, --testing, --incoming and --keyring are required"

# No error swallowing for stable: an empty or partial download used to publish an
# empty or stale repository and report success.
mkdir -p "$INCOMING/stable"
gh release download "$STABLE" --repo "$GITHUB_REPOSITORY" --dir "$INCOMING/stable" \
    --pattern "*.deb" --pattern "*.rpm"
ls -la "$INCOMING/stable"
need() {
    compgen -G "$INCOMING/stable/$1" >/dev/null || die "release $STABLE has no package matching $1"
}
need '*_amd64.deb'
need '*_arm64.deb'
need '*.x86_64.rpm'
need '*.aarch64.rpm'
if [ "$REQUIRE_ARMHF" -eq 1 ]; then need '*_armhf.deb'; fi
"$SCRIPT_DIR/check_linux_repo_packages.sh" --dir "$INCOMING/stable" --version "$STABLE" --keyring "$KEYRING"

[ "$TESTING" != "$STABLE" ] || exit 0

refuse_rc() {
    if [ "$REQUESTED" = "$TESTING" ]; then
        die "$TESTING cannot be served: $1. Nothing is deployed."
    fi
    echo "::warning::$TESTING cannot be served: $1. Testing serves $STABLE."
    rm -rf "${INCOMING:?}/testing"
    exit 0
}

# Named one by one: an rc serves no other architecture, even if someone attached
# another package to it.
mkdir -p "$INCOMING/testing"
gh release download "$TESTING" --repo "$GITHUB_REPOSITORY" --dir "$INCOMING/testing" \
    --pattern "*_amd64.deb" --pattern "*_arm64.deb" \
    --pattern "*.x86_64.rpm" --pattern "*.aarch64.rpm" \
    || refuse_rc "its download failed"
ls -la "$INCOMING/testing"
"$SCRIPT_DIR/check_linux_repo_packages.sh" --dir "$INCOMING/testing" --version "$TESTING" --keyring "$KEYRING" \
    || refuse_rc "its packages failed the checks"

SIZE_MB="$(du -sm "$INCOMING" | awk '{print $1}')"
if [ "$SIZE_MB" -gt "$SIZE_LIMIT_MB" ]; then
    refuse_rc "the site would be ${SIZE_MB} MiB, above ${SIZE_LIMIT_MB} MiB"
fi
