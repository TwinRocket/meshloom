---
title: Meshloom behind your reverse proxy
description: Caddy, nginx or Traefik in front of Meshloom, with the two settings to declare (public address, trusted proxy), the headers and the WebSocket.
level: deep
order: 24.3
---

This is the short version for a reverse proxy you already run. Other paths (Cloudflare Tunnel, Tailscale, WireGuard, Home Assistant) and the reasons behind each setting are in [Remote access on your own](/en/docs/deep/remote-self-hosted/).

From 5.0 every page and API route asks for an account, so the proxy never opens Meshloom without a sign-in ([Accounts and access](/en/docs/deep/access/)). Exposing a proxy to the Internet is still your decision: keep it updated.

## What the proxy must do

- Send everything to Meshloom's port 8000, **including the WebSocket** `/api/ws` (the page's live connection): pass the `Upgrade` and `Connection` headers. Without them the page loads and nothing arrives live.
- Keep the original `Host` (Caddy and Traefik do; nginx needs `$http_host`). A proxy that replaces it must send the original in `X-Forwarded-Host`, or the WebSocket is refused as coming from another site.
- Send `X-Forwarded-For` and `X-Forwarded-Proto`. Meshloom believes them only from an address listed in Trusted proxies.
- Do not close idle connections in under thirty seconds: the page pings every thirty seconds.
- Serve HTTPS. Web Push, passkeys and the channel finder need it ([HTTPS](/en/docs/deep/https/)).

## What to declare in Meshloom

As an administrator:

- **Settings → Users & Access → Public addresses**: add `https://meshloom.example.org` (the name people type, with `:port` if it is not 443). The [Host check](/en/docs/deep/https/#host-names-meshloom-answers-to) accepts only declared names from 5.1.
- **Settings → Local Configuration → Trusted proxies** (`trusted_proxies`): the proxy's own address, one per line. `127.0.0.1` if it runs on the same machine as a package or source install of Meshloom. Meshloom in Docker with port 8000 published: the proxy's connections then arrive from the Docker bridge gateway, which you must not list; instead put the proxy and Meshloom on a Docker network of their own, give the proxy a fixed address, list that address and publish no port 8000. Another machine: its address on your network. The list is empty by default and `127.0.0.1` is not implied. **Never list a whole Docker network or its gateway**, and never a network your visitors come from. Example Compose file: [HTTPS, Trusted proxies](/en/docs/deep/https/#trusted-proxies).
- **Settings → Users & Access → Trusted networks**: leave **Login-free on the local network** off and the list empty. It is off by default. It must stay off whenever Meshloom is reached through a proxy, or through a router port-forward that rewrites the source address (SNAT), because internet visitors would then look local. Rules never apply to a proxied request anyway ([details](/en/docs/deep/access/#trusted-networks)).

Meshloom's own server never reads forwarded headers itself; every start command carries `--no-proxy-headers`, and one you write yourself must keep it.

## Caddy

```text
meshloom.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy keeps `Host`, sets both forwarded headers and passes the WebSocket.

## nginx

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 443 ssl;
    server_name meshloom.example.org;
    # ssl_certificate / ssl_certificate_key: your certificate
    client_max_body_size 64m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }
}
```

Without the `Upgrade` and `Connection` lines the WebSocket is answered 404 and nothing arrives live. `client_max_body_size 64m;` matters: nginx refuses request bodies over 1 MB by default (413), while restoring or inspecting a backup accepts up to 64 MiB and photo shares up to 10 MiB. Caddy and Traefik set no request size limit unless you add one.

## Traefik (v3)

Dynamic configuration (file provider), with an entry point `websecure` and a certificate for the name already defined; the same router and service can be written as Docker labels (*untested end to end* in that form):

```yaml
http:
  routers:
    meshloom:
      rule: Host(`meshloom.example.org`)
      entryPoints: [websecure]
      service: meshloom
      tls: {}
  services:
    meshloom:
      loadBalancer:
        servers:
          - url: http://127.0.0.1:8000
```

`127.0.0.1:8000` assumes Traefik runs on the host. As a container, put it on a Docker network shared with Meshloom and use `http://meshloom:8000` (the service name), and list Traefik's fixed address in Trusted proxies.

Traefik keeps `Host`, sets both headers and passes the WebSocket. Leave `forwardedHeaders.trustedIPs` empty unless another proxy sits in front of it.

## Check it

Open `https://meshloom.example.org` and sign in. The sign-in page must not warn that Meshloom saw plain HTTP (if it does, Trusted proxies is missing or wrong), the session list shows your own address, and **Recently seen unknown names** stays empty. Under a path such as `/meshcore/`, declare the prefix too and read [Reverse proxy under a subpath](/en/docs/deep/https/#reverse-proxy-under-a-subpath).

## Keep it safe

- **Do not publish port 8000 or the radio proxy port.** Once the proxy fronts Meshloom, port 8000 should not be reachable any other way. The radio proxy (**Settings → Proxy**, port 5051 by default) listens on `0.0.0.0` and has no sign-in: never forward it, never put it behind the proxy ([Security](/en/docs/deep/security/#radio-proxy)).
- **Repeated failed sign-ins.** Meshloom's [built-in ban](/en/docs/deep/access/#sign-in-delays) is on by default and counts each visitor on their own once the proxy is listed in Trusted proxies. For a block at the firewall, or at the proxy's own log, see [fail2ban](/en/docs/deep/fail2ban/#behind-a-reverse-proxy-on-the-same-machine).
