---
title: Remote access on your own
description: Reach Meshloom from outside your home with your own Cloudflare Tunnel, Tailscale or WireGuard, or a reverse proxy (Caddy, nginx, Traefik), and what to declare in Meshloom.
level: deep
order: 24
---

Meshloom listens on port 8000 of the machine it runs on, and nothing more. To reach it from outside your home or office, do not forward that port on your router: put something in front of it that you control. This page covers three ways, all of them on your own account or machines, with nothing to ask from the Meshloom project. The Home Assistant add-on has [its own note](#home-assistant-add-on).

From 5.0, every page and API route asks for an account whatever the path, so a tunnel or a proxy never opens Meshloom without a sign-in. Read [Accounts and access](/en/docs/deep/access/) first, and use a strong password, [two-factor authentication](/en/docs/deep/access/#two-factor-authentication) or a [passkey](/en/docs/deep/access/#passkeys) for every administrator.

> **What was tested.** The Meshloom side of each path (which setting to change, and what Meshloom does with the headers it receives) was checked against the code of this version. The Cloudflare, Tailscale, Caddy, nginx and Traefik configurations below are the products' documented ways of doing it and have **not** been run end to end by the author of this page: they are marked *untested* where they appear. Report what you find.

## What Meshloom needs to know

Whatever you choose, four things decide whether Meshloom behaves well behind it.

| Setting | Where | What for |
|---|---|---|
| **Public addresses** | **Settings → Users & Access → Public addresses** | The name people type to reach Meshloom, written `https://name[:port][/prefix]`. Needed for the Host check ([below](#host-names)) and for passkeys. |
| **Trusted proxies** | **Settings → Local Configuration → Trusted proxies** | The address of the program that forwards to Meshloom (tunnel, proxy). Lets Meshloom see the visitor's own address and that the page is on HTTPS. |
| **Refuse unknown names (enforce)** | Same page as Public addresses | Switches the Host check from observation (the 5.0.0 default) to strict. Optional in 5.0.0. |
| **Trusted networks** | **Settings → Users & Access → Trusted networks** | Keep this list **empty** for anything reached through a tunnel or proxy ([below](#trusted-networks-off)). |

### Host names

Meshloom checks the name in the browser's address (the `Host`) to resist DNS rebinding ([Host names Meshloom answers to](/en/docs/deep/https/#host-names-meshloom-answers-to)). In 5.0.0 it only notes an unknown name in its log and in **Recently seen unknown names**; from 5.1 it will refuse it with a `421` error that names it. Declare your public name now: **Settings → Users & Access → Public addresses**, add `https://meshloom.example.org`. Only the name counts for the check; the scheme is always `https` in that field, even if you reach Meshloom over plain HTTP. Switching on **Refuse unknown names** is optional; Meshloom refuses to enable it if it would lock out the address you are using.

If you lock yourself out with a name that is now refused, open Meshloom by its IP address (always accepted), or run `meshloom-admin host-guard-observe` on the machine ([Accounts and access](/en/docs/deep/access/#commands)).

### Trusted proxies

Behind a tunnel or proxy every request reaches Meshloom from the forwarder's address. Meshloom believes `X-Forwarded-For` and `X-Forwarded-Proto` only from addresses listed in **Trusted proxies**, and the list is empty by default (`127.0.0.1` is not implied). List the forwarder's own address, one per line:

- it runs on the same machine as Meshloom (package or source install, or `cloudflared` / Caddy / `tailscale serve` as a service on the host): `127.0.0.1`, and `::1` if it connects over IPv6;
- it is another container: give it a fixed address on a network of its own and list that address (an example Compose file is in [HTTPS, Trusted proxies](/en/docs/deep/https/#trusted-proxies));
- it is another machine: its address on your network.

**Never list a whole Docker network or its gateway.** Never list a network your visitors come from. Do not publish port 8000 at all once a proxy fronts Meshloom. Details and the reasons are in [HTTPS](/en/docs/deep/https/#trusted-proxies).

Once the forwarder is listed, sign-in delays, the audit log and the session list show the visitor's address, and the session cookie becomes a secure one (`__Host-` prefix, `Secure`) when the forwarder says `X-Forwarded-Proto: https`. The sign-in page and the Trusted proxies block say so when your browser is on HTTPS but Meshloom saw plain HTTP: that message is the quickest check that this step worked.

### Trusted networks off

A trusted-network rule signs a device in without a password. It is **disabled** for any request that came through a tunnel or proxy, because every visitor from anywhere arrives from the forwarder's address: a request carrying `X-Forwarded-For`, `Forwarded`, `X-Real-IP`, `CF-Connecting-IP` or similar, or coming from a listed trusted proxy, is never signed in by a rule, and a rule never covers this machine's loopback outside the Home Assistant add-on. Meshloom also refuses to save a rule that overlaps a trusted proxy. So behind a proxy everyone signs in with an account, including you on your phone; keep your trusted-network rules, if any, for devices on the local network that reach Meshloom by its IP address ([details](/en/docs/deep/access/#trusted-networks)).

### HTTPS, HSTS and passkeys

- **Let the forwarder do HTTPS.** A tunnel or proxy that serves your name over HTTPS makes the page a secure context, which Web Push, the channel finder and passkeys need ([HTTPS](/en/docs/deep/https/)).
- **HSTS.** Behind a proxy Meshloom sends `Strict-Transport-Security` only when the proxy is listed in Trusted proxies and says `X-Forwarded-Proto: https`; otherwise it sends none and the proxy should send its own. The header pins the whole host name, on every port, for a year, so do not serve Meshloom on a name that also carries a plain-HTTP service you still use ([Security headers](/en/docs/deep/https/#security-headers)).
- **Passkeys** are offered only over https on a name declared in **Public addresses** (and, behind a proxy, only if Meshloom saw the https, so the proxy must be a trusted proxy). The passkey's relying party id is that host name, so a passkey works only on the name it was created on: changing your public name later means registering new passkeys. They are not offered on a bare IP address, nor on plain `http://` (except `http://localhost`), nor through Home Assistant ingress.
- **WebSocket.** The page's live connection is `/api/ws`. The proxy must pass the upgrade headers, and if it rewrites `Host` it must send the original one in `X-Forwarded-Host` ([WebSocket](/en/docs/deep/https/#websocket)). Caddy and Traefik need nothing; the nginx example below handles it.
- **Web Push.** Push needs HTTPS on a stable name, so it works through a tunnel or proxy on your own name and not through a bare IP address ([Push notifications](/en/docs/deep/push/#what-you-need)). Meshloom itself must also reach the browser makers' push services (Google, Mozilla, Apple) from where it runs; the tunnel does not carry that traffic.

## Cloudflare Tunnel from your account

*Untested end to end.* A Cloudflare Tunnel makes `cloudflared`, a small program on your side, open an outgoing connection to Cloudflare; Cloudflare serves your name over HTTPS and sends requests down it. Nothing is opened on your router. You need a Cloudflare account and a domain on it.

1. In Cloudflare, create a tunnel (Zero Trust → Networks → Tunnels) and install the connector on the machine that runs Meshloom (or in a container next to it).
2. Add a **public hostname**, for example `meshloom.example.org`, whose service is `http://localhost:8000` (`http://meshloom:8000` if `cloudflared` is a container on Meshloom's Docker network, with port 8000 left unpublished).
3. In Meshloom, add `https://meshloom.example.org` to **Public addresses**.
4. In **Trusted proxies**, list the address `cloudflared` connects from: `127.0.0.1` when it runs on the host; a fixed container address when it is a container.
5. Open `https://meshloom.example.org` and sign in. The sign-in page must not say that Meshloom saw plain HTTP.

Cloudflare sends `X-Forwarded-For`, `X-Forwarded-Proto` and `CF-Connecting-IP`. Meshloom reads the first two from a listed proxy and never `CF-Connecting-IP`, which only keeps trusted networks off.

**Cloudflare Access in front** (optional, recommended for a family or team): in Zero Trust, add an Access application on the same hostname with a rule such as "emails ending in". Visitors then pass Cloudflare's login before Meshloom's own. It adds a gate; it does not replace Meshloom's accounts (Meshloom does not read Access's identity). Do not apply Access to the WebSocket path separately: protect the whole hostname, or the live connection fails. An API token (`Authorization` header) used by a script cannot pass an Access login unless you create a Cloudflare service token for it, and send its two headers too.

Under Home Assistant, the **Cloudflared** add-on does the same job: see [the add-on note](#home-assistant-add-on).

## Tailscale or WireGuard

These put your phone or laptop on a private network with the machine that runs Meshloom. Nothing is published to the Internet, which is the smallest exposure of the three paths, at the cost of installing the VPN on every device that needs to reach Meshloom.

### Tailscale

*Untested end to end.*

1. Install Tailscale on the Meshloom machine and on your devices, signed in to the same tailnet.
2. **Reach Meshloom by its tailnet address**: `http://100.x.y.z:8000` (an IP address, always accepted) or `http://machine-name.tailnet-name.ts.net:8000` with MagicDNS. Declare the MagicDNS name in **Public addresses** as `https://machine-name.tailnet-name.ts.net`: the Host check wants it from 5.1, and **Recently seen unknown names** will list it with **Allow** if you forgot. Never declare a name you did not create yourself.
3. This works over plain HTTP: Tailscale encrypts the traffic between your devices, but the browser still treats the page as insecure, so there is no HTTPS-only feature (push, passkeys, channel finder) and the session cookie is the plain one. **For Web Push, passkeys and the channel finder you need HTTPS**: use `tailscale serve --bg --https=443 http://127.0.0.1:8000` (and enable HTTPS certificates for your tailnet in the Tailscale admin console), then open `https://machine-name.tailnet-name.ts.net`. Add `127.0.0.1` to **Trusted proxies** so Meshloom sees the HTTPS and the real device, and declare that name in **Public addresses**.
4. `tailscale funnel` would publish Meshloom to the whole Internet. It is not needed for this setup; Meshloom's accounts are then the only gate, so prefer `serve`.

Push to a `*.ts.net` name for the notification endpoints is a separate matter: see [Push services on a private network](/en/docs/deep/push/#push-services-on-a-private-network).

### WireGuard

*Untested end to end.* Run a WireGuard server on your router or on the Meshloom machine, with a peer per device, and open Meshloom at its address inside the tunnel (for example `http://10.8.0.1:8000`). An IP address is always accepted by the Host check, so no declaration is needed. As with Tailscale, plain HTTP means no push or passkeys: add a reverse proxy with a certificate (next section) listening on the tunnel address, or use a name you have a certificate for.

On both, a visitor reaches Meshloom directly (no forwarder), so leave Trusted proxies as it is, and note that a [trusted-network rule](/en/docs/deep/access/#trusted-networks) matching the VPN's range would sign in everyone on that VPN: do not add one.

## Your own reverse proxy

*Untested end to end* for the exact snippets; the headers they set are those listed in [HTTPS](/en/docs/deep/https/#trusted-proxies). You need a name pointing at the proxy and a certificate for it (Caddy fetches one by itself). Port 80 or 443 reaching your home from the Internet is your decision to take: the proxy is then exposed, so keep it updated.

**Caddy.** Keeps `Host`, sets both forwarded headers, passes WebSocket:

```text
meshloom.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

**nginx.** Sets nothing unless told to (keep the `Upgrade` and `Connection` lines for the live connection, and `$http_host` so the port is kept):

```nginx
server {
    listen 443 ssl;
    server_name meshloom.example.org;
    # ssl_certificate / ssl_certificate_key: your certificate

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

**Traefik** (v3, Docker provider). Keeps `Host`, sets both headers and passes WebSocket. Leave `forwardedHeaders.trustedIPs` empty unless another proxy sits in front of it. Labels on the Meshloom container, with an entry point `websecure` on `:443` and a certificate resolver named `letsencrypt` already defined in Traefik's static configuration (change both names to yours):

```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.meshloom.rule=Host(`meshloom.example.org`)
  - traefik.http.routers.meshloom.entrypoints=websecure
  - traefik.http.routers.meshloom.tls.certresolver=letsencrypt
  - traefik.http.services.meshloom.loadbalancer.server.port=8000
```

Traefik and Meshloom must share a Docker network; list Traefik's fixed address on it in Trusted proxies ([example](/en/docs/deep/https/#trusted-proxies)).

Then, in Meshloom: declare `https://meshloom.example.org` in **Public addresses** and list the proxy in **Trusted proxies** (`127.0.0.1` if it is on the host; a fixed container address otherwise). If the proxy serves Meshloom under a path such as `/meshcore/`, declare `https://meshloom.example.org/meshcore` and read [Reverse proxy under a subpath](/en/docs/deep/https/#reverse-proxy-under-a-subpath).

## Home Assistant add-on

Opened from the Home Assistant sidebar, Meshloom signs the Home Assistant user in and needs none of the above; ingress is never refused for its name and has its own address. The sidebar has no stable public address, so passkeys, trusted networks and Web Push do not work through it. To reach Meshloom from outside with the **Cloudflared** add-on (or any of the paths above): in the Meshloom add-on's **Network** panel give `8000/tcp` a host port, route the hostname to `http://<home-assistant-ip>:<that port>`, declare the hostname in **Public addresses**, then look at the sign-in page through the tunnel: if it warns that Meshloom saw plain HTTP, Trusted proxies is what is missing. **Which address to list depends on where the connection comes from.** If the tunnel is a container of its own on Home Assistant's add-on network (the Cloudflared add-on), list that container's address, as Meshloom sees it (the audit log and **My sessions** show it). If the connection arrives through the published host port, Meshloom usually sees the Docker gateway of that network instead: **do not list it** (the rule above), leave Trusted proxies empty and accept that visitors share one address and that the session cookie is the plain one, or route the tunnel to the add-on's container address directly rather than to the published port. The address is not verified on Home Assistant hardware by the author. This address asks for a Meshloom account, not a Home Assistant login. See [Home Assistant](/en/docs/deep/access/#home-assistant) and the install note in [Install](/en/docs/install/).

## Checklist

- Accounts exist, administrators have a strong password and a second factor or a passkey.
- The page is served in HTTPS by the forwarder, and the sign-in page does not warn about plain HTTP.
- The public name is in **Public addresses**; the forwarder's own address, and nothing wider, is in **Trusted proxies**.
- **Trusted networks** is empty, or holds only local devices reached by IP address.
- Port 8000 and the radio proxy port (5051) are not forwarded on your router. The radio proxy has no authentication: see [Security](/en/docs/deep/security/).
