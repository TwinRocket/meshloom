---
title: HTTPS
description: Local TLS, WebGPU, and Meshloom behind a reverse proxy.
level: deep
order: 13
---

Meshloom works over plain HTTP for local use. Three features require HTTPS or `localhost`.

## What requires HTTPS

A browser secure context is an HTTPS page or a page served from `localhost`. Plain HTTP on a LAN IP is not secure:

- WebGPU channel-key search does not work there.
- [Push notifications](/en/docs/deep/push/) require HTTPS because the service worker does.
- Optional HTTP Basic credentials would otherwise travel in clear text. See [Security](/en/docs/deep/security/).

A self-signed certificate is enough for all three.

## Local certificate and uvicorn

```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=key.pem --ssl-certfile=cert.pem
```

The browser will show a warning. [mkcert](https://github.com/FiloSottile/mkcert) creates locally trusted certificates.

## Docker Compose

```yaml
services:
  meshloom:
    volumes:
      - ./data:/app/data
      - ./cert.pem:/app/cert.pem:ro
      - ./key.pem:/app/key.pem:ro
    command: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=/app/key.pem --ssl-certfile=/app/cert.pem
```

The read-only mounts are intentional. `command` replaces the image command, so include the full host and port.

## Reverse proxy under a subpath

Meshloom can run under a prefix such as `/meshcore/`, including through the Home Assistant ingress. Keep the trailing slash. Asset, API, and web-manifest URLs are relative (`start_url` and `scope` are `./`), so no `X-Forwarded-*` header is needed.

## WebSocket

The live feed uses `/api/ws`. The proxy must pass upgrade headers. Otherwise REST history loads but live messages do not, and the client reconnects every three seconds. The client pings every 30 seconds, so shorter idle timeouts cause loops. Basic auth covers WebSocket as well as HTTP.

## Which host to serve

`--host 0.0.0.0` exposes the server on every interface, which is useful for phones and other computers but makes the [Security](/en/docs/deep/security/) warnings relevant. For local-only access, keep the default host and use `localhost` without TLS.
