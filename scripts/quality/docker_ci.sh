#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Floor: Node 22 / npm 10, the same minimum install_service.sh enforces for a
# local build (vite 8 needs Node >= 22.12 on the 22 line; Node 20 is EOL).
# Node 26 is what CI, the Docker image, the .deb and the release zip use.
NODE_VERSIONS=("22" "24" "26")
# Use explicit npm patch versions so resolver regressions are caught.
NPM_VERSIONS=("10.9.5" "11.6.2")
EXTRA_CASES=(
    "24|11.12.0"
    "26|11.20.0"
)

run_combo() {
    local node_version="$1"
    local npm_version="$2"
    local image="node:${node_version}-slim"

    docker run --rm \
        -v "$REPO_ROOT:/src:ro" \
        -w /tmp \
        "$image" \
        bash -lc "
            set -euo pipefail
            cp -a /src/frontend ./frontend
            cd frontend
            npm i -g npm@${npm_version}
            echo 'Using Node:' \$(node -v)
            echo 'Using npm:' \$(npm -v)
            npm ci
            npm run build
        "
}

declare -a TEST_CASES=()
declare -A SEEN_CASES=()

add_case() {
    local node_version="$1"
    local npm_version="$2"
    local key="${node_version}|${npm_version}"
    if [[ -n "${SEEN_CASES[$key]:-}" ]]; then
        return
    fi
    SEEN_CASES["$key"]=1
    TEST_CASES+=("$key")
}

for node_version in "${NODE_VERSIONS[@]}"; do
    for npm_version in "${NPM_VERSIONS[@]}"; do
        add_case "$node_version" "$npm_version"
    done
done

for case_spec in "${EXTRA_CASES[@]}"; do
    IFS='|' read -r node_version npm_version <<<"$case_spec"
    add_case "$node_version" "$npm_version"
done

TMP_DIR="$(mktemp -d)"
declare -a JOB_PIDS=()
declare -a JOB_LABELS=()
declare -a JOB_LOGS=()

cleanup() {
    rm -rf "$TMP_DIR"
}

trap cleanup EXIT

echo -e "${YELLOW}=== Frontend Docker CI Matrix ===${NC}"
echo -e "${BLUE}Repo:${NC} $REPO_ROOT"
echo

for case_spec in "${TEST_CASES[@]}"; do
    IFS='|' read -r node_version npm_version <<<"$case_spec"
    label="Node ${node_version} / npm ${npm_version}"
    safe_npm="${npm_version//./-}"
    log_file="$TMP_DIR/node-${node_version}-npm-${safe_npm}.log"

    echo -e "${BLUE}Starting:${NC} ${label}"
    (
        echo -e "${YELLOW}=== ${label} ===${NC}"
        run_combo "$node_version" "$npm_version"
    ) >"$log_file" 2>&1 &

    JOB_PIDS+=("$!")
    JOB_LABELS+=("$label")
    JOB_LOGS+=("$log_file")
done

echo

failures=0
for idx in "${!JOB_PIDS[@]}"; do
    if wait "${JOB_PIDS[$idx]}"; then
        echo -e "${GREEN}Passed:${NC} ${JOB_LABELS[$idx]}"
    else
        failures=$((failures + 1))
        echo -e "${RED}Failed:${NC} ${JOB_LABELS[$idx]}"
        echo -e "${YELLOW}--- ${JOB_LABELS[$idx]} log ---${NC}"
        cat "${JOB_LOGS[$idx]}"
        echo
    fi
done

if (( failures > 0 )); then
    echo -e "${RED}=== Docker CI matrix failed (${failures} job(s)) ===${NC}"
    exit 1
fi

echo -e "${GREEN}=== Docker CI matrix passed ===${NC}"
