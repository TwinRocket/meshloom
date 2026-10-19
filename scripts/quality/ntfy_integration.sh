#!/usr/bin/env bash
# Runs tests/integration/test_ntfy_e2e.py against a real ntfy server
# (binwiederhier/ntfy) in a throwaway container. Needs Docker and `uv sync`.
# Also reachable as `docker_ci.sh --ntfy`. MESHLOOM_NTFY_URL is a developer-only
# variable of that test (see tests/integration/test_ntfy_e2e.py).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="${NTFY_IMAGE:-binwiederhier/ntfy:v2.11.0@sha256:4a7d0f0adc6d5d9fc36e64ab55ef676e76e124a2bdd50ce115b6d9c1c7430294}"
NAME="meshloom-ntfy-ci-$$"

cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$NAME" -p 127.0.0.1:0:80 "$IMAGE" serve \
    --listen-http :80 --cache-file "" >/dev/null
PORT="$(docker port "$NAME" 80/tcp | head -n1 | sed 's/.*://')"
URL="http://127.0.0.1:${PORT}"

for _ in $(seq 1 60); do
    if curl -fsS "$URL/v1/health" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
curl -fsS "$URL/v1/health" >/dev/null || { docker logs "$NAME"; echo "ntfy did not start" >&2; exit 1; }

cd "$REPO_ROOT"
MESHLOOM_NTFY_URL="$URL" PYTHONPATH=. uv run pytest tests/integration/test_ntfy_e2e.py -v -n 0
