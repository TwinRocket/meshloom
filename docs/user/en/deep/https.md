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
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers --ssl-keyfile=key.pem --ssl-certfile=cert.pem
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
    command: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers --ssl-keyfile=/app/key.pem --ssl-certfile=/app/cert.pem
```

The certificate mounts are read-only. `command` replaces the one built into the image, so it must contain the whole line, host and port included.

## Trusted proxies

Behind a reverse proxy, every request reaches Meshloom from the proxy's address. The visitor's own address and whether they used HTTPS only arrive in the `X-Forwarded-For` and `X-Forwarded-Proto` headers, which anyone can send. Meshloom believes them only from the addresses listed in **Settings → Local Configuration → Trusted proxies** (administrators only). The list is empty by default, and `127.0.0.1` is not trusted unless you add it.

List the address the proxy connects from, as Meshloom sees it:

- the proxy runs on the same machine as a package or source install: `127.0.0.1` (and `::1` if it connects over IPv6);
- the proxy is another container on the same Docker network: that network, for example `172.18.0.0/16` (`docker network inspect <network>` shows it);
- the proxy is another machine: its address on your network.

Once the proxy is listed:

- sign-in delays, the audit log and the session list show the visitor's address instead of the proxy's. Meshloom takes the rightmost address of `X-Forwarded-For` that is not itself a trusted proxy, so a value the visitor wrote cannot be picked;
- when the proxy says `X-Forwarded-Proto: https`, the session cookie is a secure one (`__Host-` prefix, `Secure`).

Without the list, Meshloom still works behind a proxy, but every visitor shares the proxy's address and, on an HTTPS site, the session cookie is the plain one. Never list a network your visitors come from: a listed address can claim to be anyone. Meshloom's own server (uvicorn) reads none of these headers itself: every start command carries `--no-proxy-headers`, and a command you write yourself must keep it.

Caddy keeps `Host` and sets both headers by itself:

```text
meshloom.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

Nginx sets nothing unless told to:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

Traefik keeps `Host` and sets both headers; it passes on the headers a client sent only from its own `forwardedHeaders.trustedIPs`, so leave that empty unless another proxy sits in front of it. With Docker labels:

```yaml
labels:
  - traefik.http.routers.meshloom.rule=Host(`meshloom.example.org`)
  - traefik.http.services.meshloom.loadbalancer.server.port=8000
```

The Home Assistant sidebar needs no entry: Meshloom recognises the Supervisor by its own address.

## Reverse proxy under a subpath

Meshloom can be served under a prefix such as `/meshcore/`, including through the Home Assistant sidebar. Addresses of assets, API and web manifest are all relative, so nothing else needs to be configured and no `X-Forwarded-*` header is required, except `X-Forwarded-Host` for the live connection when the proxy rewrites `Host` (see [WebSocket](#websocket)).

One thing is required of the proxy: the address must **end with a slash**. If a visitor reaches `/meshcore` without it, the relative paths break. Most proxies handle this; with Nginx, a `location /meshcore/ { ... }` block, slash included, does the job.

## WebSocket

The page keeps one permanent connection to the server, at `/api/ws`, to receive messages, receipts, packets and the radio state as they happen. A proxy that does not pass the connection-upgrade headers cuts it. The typical symptom: the page displays, the history loads, but nothing arrives live. The browser keeps retrying, waiting a little longer each time (from one second up to thirty).

The page pings the server every thirty seconds. A proxy that closes idle connections sooner than that makes the page reconnect over and over.

When HTTP Basic login is on, it also applies to this connection, not only to the pages.

The server refuses this connection when it comes from a page of another site: a browser says which site the page belongs to (the `Origin` header), and it must match the address the browser asked for (the `Host` header), unless that site is one of the **Allowed origins** (see [Security](/en/docs/deep/security/)). Scripts and apps that send no `Origin` are not affected. A proxy that keeps the original `Host`, as Caddy and Traefik do, needs nothing more. A proxy that replaces it must send the original one in `X-Forwarded-Host` (with Nginx: `proxy_set_header Host $http_host;` or `proxy_set_header X-Forwarded-Host $http_host;`; `$host` would drop the port); the Home Assistant sidebar already does. Otherwise the page displays but nothing arrives live, and the server log says `Refused a WebSocket upgrade from a foreign origin`.

## Which address to listen on

`--host 0.0.0.0` makes the server reachable from every network the machine is on. This is what the Linux package, the installer's Docker setup and the image do, because the point is to use Meshloom from a phone or another computer. It is also why the warnings of [Security](/en/docs/deep/security/) matter: there are no user accounts, and where requests come from is not restricted.

If you run it by hand and want it reachable from the machine itself only, use the uvicorn default (`127.0.0.1`) and open `http://localhost:8000`, which is a secure context without any certificate.
