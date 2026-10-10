#!/usr/bin/env bash
# Fail unless every place that carries the release version equals the tag.
#
# Usage: scripts/build/check_version_consistency.sh X.Y.Z
# Sources: pyproject.toml, frontend/package.json, meshloom/config.yaml
# (Home Assistant add-on) and the FROM tag of meshloom/Dockerfile.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="${MESHLOOM_VERSION_ROOT:-$REPO_ROOT}"
EXPECTED="${1:-}"
[[ $EXPECTED =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: $0 X.Y.Z" >&2; exit 2; }

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
check "meshloom/config.yaml version" "$(sed -n 's/^version: "\(.*\)"$/\1/p' "$ROOT/meshloom/config.yaml" | head -n 1)"
check "meshloom/Dockerfile FROM tag" "$(sed -n 's|^FROM ghcr.io/twinrocket/meshloom:\([^ @]*\).*$|\1|p' "$ROOT/meshloom/Dockerfile" | head -n 1)"
exit "$rc"
