#!/bin/sh
# publish.sh <tag>: make <tag> the "latest release" of the fake GitHub.
C=$(cd "$(dirname "$0")/.." && pwd)
echo "$1" >"$C/gh/latest"
docker exec e2ec-gh rm -rf /srv/gh && docker cp "$C/gh" e2ec-gh:/srv/gh
