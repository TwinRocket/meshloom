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

## Host names Meshloom answers to

A web page on someone else's domain can be re-pointed at your local network (DNS rebinding) and then talk to Meshloom under that domain's name. Meshloom therefore looks at the name in the browser's address (the `Host`) and knows these as its own: an IP address, `localhost`, names ending in `.local`, `.home.arpa`, `.lan` or `.internal`, this machine's name, a public address you declared, and the Meshloom Remote name.

In 5.0.0 this is **observation only**: any other name (for example the DNS name of your reverse proxy) still works, Meshloom just notes it in its log and keeps a list of the names it saw. From 5.1 an unknown name will be refused with an error that names it, so declare your reverse proxy's public address before then. The one exception is already strict: a [trusted network](/en/docs/deep/access/#trusted-networks) never signs anyone in when the browser uses an unknown name.

### Declaring public addresses

An administrator manages this in **Settings → Users & Access → Public addresses**; there is no environment variable for it.

- **Public addresses** are written `https://name[:port][/prefix]` (letters, digits, dots and hyphens, or an international name such as `bücher.de`, which Meshloom converts to its `xn--` form; an optional path prefix of letters, digits and `-._~` only; no login, query or fragment; 16 at most). Meshloom stores them in canonical form (lower case, `:443` dropped). Only the name matters to the Host check.
- **Recently seen unknown names** lists the names used to reach Meshloom that are not declared (since the last restart, newest first, with a count). Strict mode records them too, so the list shows what was refused. These names are supplied by whoever sent the request, so an unknown one may come from an attacker (DNS rebinding): **Allow** asks you to confirm the exact name, with its count and last time, and you should allow only a name you set up yourself (your reverse proxy, your Tailscale name). Confirming declares it as a public address and removes it from the list.
- **Refuse unknown names (enforce)** switches the check from observation to strict: a request under an undeclared name gets an error (421) that names it. Meshloom refuses to enable it when it would reject the address you are using right now, and likewise to remove that address while it is enforced. If a name stops working anyway, open Meshloom by its IP address (always accepted) and turn the option off. If you can only reach Meshloom through a name that is now refused, run `meshloom-admin host-guard-observe` on the machine (see [Users and access](/en/docs/deep/access/)): it switches back to observation without the interface, and the running Meshloom picks it up within a couple of seconds (no restart).

Only plain names (letters, digits, dots, hyphens) are listed and logged; other `Host` values are never recorded. The log keeps a single line per new name, and a summary line when many different names show up in a minute.

## Trusted proxies

Behind a reverse proxy, every request reaches Meshloom from the proxy's address. The visitor's own address and whether they used HTTPS only arrive in the `X-Forwarded-For` and `X-Forwarded-Proto` headers, which anyone can send. Meshloom believes them only from the addresses listed in **Settings → Local Configuration → Trusted proxies** (administrators only). The list is empty by default, and `127.0.0.1` is not trusted unless you add it.

List the proxy's own address, as Meshloom sees it, one address per line (a `/32`, or a `/128` in IPv6):

- the proxy runs on the same machine as a package or source install: `127.0.0.1` (and `::1` if it connects over IPv6);
- the proxy is another container: give it a fixed address on a network of its own and list that address, for example `172.20.0.10` (see below);
- the proxy is another machine: its address on your network.

**Never list a whole Docker network or its gateway** (`172.18.0.0/16`, `172.17.0.1`, Docker Desktop's `192.168.65.1`). When Meshloom's port 8000 is also published on the host, some clients reach it through that gateway (connections from the host itself, IPv6 through `docker-proxy`, Docker Desktop): with the gateway trusted, they could write any address in `X-Forwarded-For` and pass the sign-in delays or falsify the audit log. The settings page warns about such an entry. Once a proxy fronts Meshloom, do not publish port 8000 at all if you can.

With Docker Compose, a fixed proxy address looks like this:

```yaml
services:
  meshloom:
    networks: [proxy]
    # no "ports:" once the proxy fronts Meshloom
  caddy:
    image: caddy:2
    ports: ["443:443", "80:80"]
    networks:
      proxy:
        ipv4_address: 172.20.0.10   # list this address in Trusted proxies
networks:
  proxy:
    ipam:
      config:
        - subnet: 172.20.0.0/24
```

Once the proxy is listed:

- sign-in delays, the audit log and the session list show the visitor's address instead of the proxy's. Meshloom takes the rightmost address of `X-Forwarded-For` that is not itself a trusted proxy, so a value the visitor wrote cannot be picked;
- when the proxy says `X-Forwarded-Proto: https`, the session cookie is a secure one (`__Host-` prefix, `Secure`).

Without the list, Meshloom still works behind a proxy, but every visitor shares the proxy's address and, on an HTTPS site, the session cookie is the plain one. The sign-in page and the Trusted proxies block say so when your browser is on HTTPS but Meshloom saw plain HTTP. Never list a network your visitors come from: a listed address can claim to be anyone. Meshloom's own server (uvicorn) reads none of these headers itself: every start command carries `--no-proxy-headers`, and a command you write yourself must keep it.

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

## Security headers

When the browser reaches Meshloom directly over HTTPS, Meshloom adds `Strict-Transport-Security` (one year, no subdomains) so the browser keeps using HTTPS for that host name, **on every port**: another service on the same name (for example `http://homeassistant.local:8123`) is then also forced to HTTPS for a year. Browsers ignore it for a bare IP address. It never sends it over plain HTTP, so a local address such as `http://192.168.1.20:8000` is not pinned to HTTPS, and never through the Home Assistant sidebar or the relay, where the address belongs to someone else. Behind a reverse proxy the header is sent only when the proxy is listed in [Trusted proxies](#trusted-proxies) and says `X-Forwarded-Proto: https`; otherwise let the proxy send its own.

Sign-in answers (`/api/auth/`) and the answers that carry secrets (the radio private key, backups, channel keys) carry `Cache-Control: no-store`, so no browser or proxy keeps them. A `Permissions-Policy` turns the camera and microphone off for the page and leaves the position button its location access.

## Reverse proxy under a subpath

Meshloom can be served under a prefix such as `/meshcore/`, including through the Home Assistant sidebar. Addresses of assets, API and web manifest are all relative, so nothing else needs to be configured and no `X-Forwarded-*` header is required, except `X-Forwarded-Host` for the live connection when the proxy rewrites `Host` (see [WebSocket](#websocket)).

One thing is required of the proxy: the address must **end with a slash**. If a visitor reaches `/meshcore` without it, the relative paths break. Most proxies handle this; with Nginx, a `location /meshcore/ { ... }` block, slash included, does the job.

## WebSocket

The page keeps one permanent connection to the server, at `/api/ws`, to receive messages, receipts, packets and the radio state as they happen. A proxy that does not pass the connection-upgrade headers cuts it. The typical symptom: the page displays, the history loads, but nothing arrives live. The browser keeps retrying, waiting a little longer each time (from one second up to thirty).

The page pings the server every thirty seconds. A proxy that closes idle connections sooner than that makes the page reconnect over and over.

When HTTP Basic login is on, it also applies to this connection, not only to the pages.

The server refuses this connection when it comes from a page of another site: a browser says which site the page belongs to (the `Origin` header), and it must match the address the browser asked for (the `Host` header), unless that site is one of the **Allowed origins** (see [Security](/en/docs/deep/security/)). Scripts and apps that send no `Origin` are not affected. A proxy that keeps the original `Host`, as Caddy and Traefik do, needs nothing more. A proxy that replaces it must send the original one in `X-Forwarded-Host` (with Nginx: `proxy_set_header Host $http_host;` or `proxy_set_header X-Forwarded-Host $http_host;`; `$host` would drop the port); the Home Assistant sidebar already does. Otherwise the page displays but nothing arrives live, and the server log says `Refused a WebSocket upgrade from a foreign origin`.

## Which address to listen on

`--host 0.0.0.0` makes the server reachable from every network the machine is on. This is what the Linux package, the installer's Docker setup and the image do, because the point is to use Meshloom from a phone or another computer. It is also why the warnings of [Security](/en/docs/deep/security/) matter: an account is the only gate, and the server does not restrict which addresses may reach it.

If you run it by hand and want it reachable from the machine itself only, use the uvicorn default (`127.0.0.1`) and open `http://localhost:8000`, which is a secure context without any certificate.
