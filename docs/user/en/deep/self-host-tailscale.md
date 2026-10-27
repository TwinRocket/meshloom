---
title: Tailscale serve in front of Meshloom
description: Reach Meshloom over HTTPS from your own devices with tailscale serve, and what to declare in Meshloom (public address, trusted proxy).
level: deep
order: 24.2
---

`tailscale serve` publishes Meshloom to the devices of your own tailnet only, over HTTPS, with a certificate for your MagicDNS name. Nothing is opened on your router. This page is the short version for a Meshloom running on the same machine as Tailscale. Other ways in (Cloudflare Tunnel, WireGuard) and the reasons behind each setting are in [Remote access on your own](/en/docs/deep/remote-self-hosted/).

From 5.0 every page and API route asks for an account, so this never opens Meshloom without a sign-in ([Accounts and access](/en/docs/deep/access/)).

## Steps

1. In the Tailscale admin console, turn on MagicDNS and HTTPS certificates for your tailnet.
2. On the Meshloom machine, publish port 8000:

   ```bash
   tailscale serve --bg --https=443 http://127.0.0.1:8000
   ```

   Your name is `machine-name.tailnet-name.ts.net` (`tailscale serve status` shows it).
3. In Meshloom, as an administrator:
   - **Settings → Users & Access → Public addresses**: add `https://machine-name.tailnet-name.ts.net`. This declares the MagicDNS name as yours, so the [Host check](/en/docs/deep/https/#host-names-meshloom-answers-to) accepts it (from 5.1 it refuses undeclared names). Only add a name you created.
   - **Settings → Local Configuration → Trusted proxies**: add `127.0.0.1`. `tailscale serve` connects from the machine itself and tells Meshloom the visitor's address and that the page is HTTPS only in `X-Forwarded-For` and `X-Forwarded-Proto`, which Meshloom believes only from a listed address. Without it, every device looks like `127.0.0.1` and the session cookie is the plain one.
4. Open `https://machine-name.tailnet-name.ts.net` from another device of the tailnet and sign in. The sign-in page must not warn that Meshloom saw plain HTTP. If it does, Trusted proxies is missing or wrong.

If Meshloom runs in Docker, the address it sees depends on how port 8000 is published: read [HTTPS, Trusted proxies](/en/docs/deep/https/#trusted-proxies) before listing anything, and never list a Docker gateway or network.

## Keep it safe

- **Leave Login-free on the local network off** (**Settings → Users & Access → Trusted networks**) and the rule list empty. It is off by default. Behind `tailscale serve` a rule never applies (the request comes through a proxy), but the switch must stay off whenever Meshloom is exposed through a proxy, or through a router port-forward that rewrites the source address (SNAT): internet visitors would then look local. A rule over `100.64.0.0/10` would cover your whole tailnet. See [Trusted networks](/en/docs/deep/access/#trusted-networks).
- **Do not use `tailscale funnel`.** It publishes Meshloom to the whole Internet, and your accounts become the only gate.
- **The radio proxy has no sign-in.** If you turn on **Settings > Proxy**, it listens on `0.0.0.0` (port 5051 by default) and every device that can reach this machine on the tailnet can use your radio, whatever `tailscale serve` publishes. Keep it off, or restrict the port with Tailscale ACLs ([Security](/en/docs/deep/security/#radio-proxy)).
- **Repeated failed sign-ins.** Meshloom's [built-in ban](/en/docs/deep/access/#sign-in-delays) is on by default. [fail2ban](/en/docs/deep/fail2ban/) adds nothing here: only your own devices connect.
