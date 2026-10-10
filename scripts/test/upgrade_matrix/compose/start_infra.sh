#!/bin/sh
# Fake GitHub (172.31.250.20) and fake ghcr.io registry (172.31.250.21) on e2e-net.
set -eu
C=$(cd "$(dirname "$0")/.." && pwd)
docker rm -f e2ec-gh e2ec-reg >/dev/null 2>&1 || true
docker network inspect e2e-net >/dev/null 2>&1 || docker network create --subnet 172.31.250.0/24 e2e-net
docker create --name e2ec-gh --network e2e-net --ip 172.31.250.20 python:3.12-slim \
    python3 -u /srv/gh_server.py /srv/gh /srv/tls/srv.crt /srv/tls/srv.key >/dev/null
docker cp "$C/scripts/gh_server.py" e2ec-gh:/srv/gh_server.py
docker cp "$C/tls" e2ec-gh:/srv/tls
mkdir -p "$C/gh"; [ -f "$C/gh/latest" ] || echo 4.17.0 >"$C/gh/latest"
docker cp "$C/gh" e2ec-gh:/srv/gh
docker start e2ec-gh >/dev/null
docker create --name e2ec-reg --network e2e-net --ip 172.31.250.21 \
    -e REGISTRY_HTTP_ADDR=0.0.0.0:443 -e REGISTRY_HTTP_TLS_CERTIFICATE=/certs/srv.crt \
    -e REGISTRY_HTTP_TLS_KEY=/certs/srv.key registry:2 >/dev/null
docker cp "$C/tls" e2ec-reg:/certs
docker start e2ec-reg >/dev/null
echo infra up
