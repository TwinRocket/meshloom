#!/usr/bin/env bash
# Fail unless every place that carries the release version equals the tag.
#
# Usage: scripts/build/check_version_consistency.sh X.Y.Z|X.Y.Z-rc.N
# Sources: pyproject.toml, frontend/package.json, meshloom/config.yaml
# (Home Assistant add-on) and the FROM tag of meshloom/Dockerfile.
#
# An rc leaves the add-on alone: Home Assistant reads config.yaml from the default
# branch, so it must keep naming the last stable release, in both of its files.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="${MESHLOOM_VERSION_ROOT:-$REPO_ROOT}"
EXPECTED="${1:-}"
STABLE_RE='^[0-9]+\.[0-9]+\.[0-9]+$'
RC_RE='^[0-9]+\.[0-9]+\.[0-9]+-rc\.[1-9][0-9]*$'
[[ $EXPECTED =~ $STABLE_RE ]] || [[ $EXPECTED =~ $RC_RE ]] \
    || { echo "usage: $0 X.Y.Z|X.Y.Z-rc.N" >&2; exit 2; }

rc=0
check() {
    local label="$1" actual="$2"
    if [ "$actual" = "$EXPECTED" ]; then
        echo "ok    $label = $actual"
    else
        echo "::error::$label is '${actual:-<missing>}', expected $EXPECTED"
        rc=1
    fi
}

check "pyproject.toml version" "$(sed -n 's/^version = "\(.*\)"$/\1/p' "$ROOT/pyproject.toml" | head -n 1)"
check "frontend/package.json version" "$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version",""))' "$ROOT/frontend/package.json")"

ADDON_VERSION="$(sed -n 's/^version: "\(.*\)"$/\1/p' "$ROOT/meshloom/config.yaml" | head -n 1)"
ADDON_IMAGE="$(sed -n 's|^FROM ghcr.io/twinrocket/meshloom:\([^ @]*\).*$|\1|p' "$ROOT/meshloom/Dockerfile" | head -n 1)"
if [[ $EXPECTED =~ $RC_RE ]]; then
    if [[ $ADDON_VERSION =~ $STABLE_RE ]] && [ "$ADDON_IMAGE" = "$ADDON_VERSION" ]; then
        echo "ok    add-on stays on stable release $ADDON_VERSION"
    else
        echo "::error::the add-on must stay on a stable release for an rc: meshloom/config.yaml is '${ADDON_VERSION:-<missing>}', meshloom/Dockerfile FROM is '${ADDON_IMAGE:-<missing>}'"
        rc=1
    fi
else
    check "meshloom/config.yaml version" "$ADDON_VERSION"
    check "meshloom/Dockerfile FROM tag" "$ADDON_IMAGE"
fi
exit "$rc"
