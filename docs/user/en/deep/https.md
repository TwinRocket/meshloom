---
title: HTTPS
description: Local TLS, WebGPU, and Meshloom behind a reverse proxy.
level: deep
order: 13
---

Meshloom works over plain HTTP, and for local use that is enough. Three features need more.

## What requires HTTPS

Browsers reserve some features for a **secure context**: a page served over HTTPS, or from `localhost`. A page served over plain HTTP from a local network address such as `192.168.1.20` is not a secure context.

On such a page:

- The channel finder's brute-force search does not work, because it needs WebGPU. The page says so.
- [Push notifications](/en/docs/deep/push/) do not work, because they need a service worker, and browsers only allow those in a secure context. **Settings → Notifications** says that push needs HTTPS.
- The optional HTTP Basic login would send the password in clear text. See [Security](/en/docs/deep/security/).

A self-signed certificate (one you made yourself) makes the page a secure context, which is enough for the channel finder. For push, your browser must also accept the certificate. Meshloom warns that delivery can be unreliable with an untrusted certificate, depending on the browser. A certificate your devices trust, such as one made with [mkcert](https://github.com/FiloSottile/mkcert), avoids the problem.

## Local certificate and uvicorn

Uvicorn is the program that serves Meshloom. Make a certificate, then start it with the two files:

```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=key.pem --ssl-certfile=cert.pem
```

The browser shows a warning because nobody known signed the certificate. Accept it once. [mkcert](https://github.com/FiloSottile/mkcert) makes certificates your own devices trust, which avoids the warning.

## Docker Compose

Make the certificate on the host, mount it into the container, and replace the start command:

```yaml
services:
  meshloom:
    volumes:
      - ./data:/app/data
      - ./cert.pem:/app/cert.pem:ro
      - ./key.pem:/app/key.pem:ro
    command: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=/app/key.pem --ssl-certfile=/app/cert.pem
```

The certificate mounts are read-only. `command` replaces the one built into the image, so it must contain the whole line, host and port included.

## Reverse proxy under a subpath

Meshloom can be served under a prefix such as `/meshcore/`, including through the Home Assistant sidebar. Addresses of assets, API and web manifest are all relative, so nothing else needs to be configured and no `X-Forwarded-*` header is required.

One thing is required of the proxy: the address must **end with a slash**. If a visitor reaches `/meshcore` without it, the relative paths break. Most proxies handle this; with Nginx, a `location /meshcore/ { ... }` block, slash included, does the job.

## WebSocket

The page keeps one permanent connection to the server, at `/api/ws`, to receive messages, receipts, packets and the radio state as they happen. A proxy that does not pass the connection-upgrade headers cuts it. The typical symptom: the page displays, the history loads, but nothing arrives live. The browser keeps retrying, waiting a little longer each time (from one second up to thirty).

The page pings the server every thirty seconds. A proxy that closes idle connections sooner than that makes the page reconnect over and over.

When HTTP Basic login is on, it also applies to this connection, not only to the pages.

## Which address to listen on

`--host 0.0.0.0` makes the server reachable from every network the machine is on. This is what the Linux package, the installer's Docker setup and the image do, because the point is to use Meshloom from a phone or another computer. It is also why the warnings of [Security](/en/docs/deep/security/) matter: there are no user accounts, and where requests come from is not restricted.

If you run it by hand and want it reachable from the machine itself only, use the uvicorn default (`127.0.0.1`) and open `http://localhost:8000`, which is a secure context without any certificate.
