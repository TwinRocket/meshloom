---
title: Blocking repeated sign-in failures with fail2ban
description: Turn on the fail2ban jail shipped with the Linux packages, on Docker, or on the reverse proxy in front of Meshloom.
level: deep
order: 24.5
---

Meshloom already slows down someone who keeps guessing a password ([Sign-in delays](/en/docs/deep/access/#sign-in-delays)). [fail2ban](https://github.com/fail2ban/fail2ban) adds a block at the firewall: an address that fails five times in ten minutes can no longer reach port 8000 at all, for an hour, then longer each time it comes back. It is optional, and off until you turn it on.

Each refused password, code, passkey, token or installation code writes one line starting with `AUTH_FAIL` to Meshloom's log, with the address it came from. fail2ban watches those lines. Nothing in them identifies the account: only a short code that changes at each restart.

## The built-in ban and fail2ban

Meshloom also has a [built-in ban](/en/docs/deep/access/#sign-in-delays), on by default: 10 refused sign-ins within 10 minutes from one address ban it for 60 minutes. It counts the same `AUTH_FAIL` lines, works wherever Meshloom runs, Docker included (sign-ins through Home Assistant are never counted) and needs nothing installed. An administrator tunes it, lifts bans and keeps a whitelist in **Settings → Users & Access → Address bans**, or with `meshloom-admin unban`.

The difference is where the block happens. The built-in ban answers a banned address with an error before checking any password: it still reaches Meshloom, which costs little. fail2ban blocks it at the firewall, so it no longer reaches the port at all, and can ban longer each time it comes back.

- **The built-in ban alone** is enough for most installations, and is the only choice in Docker without journald, or on macOS or Windows.
- **Both** on a Linux machine open to the internet: the built-in ban acts at once, fail2ban keeps a stubborn address off the machine. They do not get in each other's way.

## What the Linux packages install

The `.deb` and `.rpm` packages carry two files, both kept when you upgrade (an edit of yours is never overwritten):

- `/etc/fail2ban/filter.d/meshloom.conf`: how to read the line.
- `/etc/fail2ban/jail.d/meshloom.conf`: the jail, **disabled** (`enabled = false`). It reads the systemd journal of `meshloom.service`, bans after 5 failures in 10 minutes for 1 hour, doubles the ban each time the address returns (at most a week), and covers port 8000.

fail2ban itself is only suggested: Meshloom works without it, and installing the package does not install it.

## Turn it on (Linux package)

1. Install fail2ban: `sudo apt install fail2ban` or `sudo dnf install fail2ban` (on RHEL, AlmaLinux and Rocky it comes from EPEL; Fedora has it in its own repositories).
2. Create `/etc/fail2ban/jail.d/meshloom.local` with this content (the shipped `.conf` stays untouched, so an upgrade never asks you about it):

   ```ini
   [meshloom]
   enabled = true
   ```

   If you changed the port with `--port` in the service, add `port = ` and your port.
3. `sudo fail2ban-client reload`, then `sudo fail2ban-client status meshloom` shows the jail.
4. To test, fail a sign-in 5 times from another computer, then look at `sudo fail2ban-client status meshloom`: its address is in the list. `sudo fail2ban-client set meshloom unbanip <address>` lifts the ban.

To never ban an address of yours, add `ignoreip = 127.0.0.1/8 ::1 192.168.1.0/24` under `[meshloom]` in the same `.local` file (your own network).

## Behind a reverse proxy on the same machine

List the proxy in [Trusted proxies](/en/docs/deep/https/#trusted-proxies) first. Until you do, every visitor looks like the proxy: when the proxy sends forwarding headers (they all do), Meshloom marks the line `proxied=1` and the filter **ignores it**, because banning your own proxy would lock everyone out. Once the proxy is declared, the line carries the visitor's address. A firewall ban on this machine then blocks the proxy's traffic from that visitor's address only if the proxy forwards the visitor's own address, which a normal reverse proxy does not: the firewall sees the proxy as the source. To block at the proxy, use the next section.

## Docker

The container logs to its standard error, so the jail changes in two places: where it reads and where it blocks. Put both in `/etc/fail2ban/jail.d/meshloom.local`.

- **Where it reads.** Docker's default `json-file` driver wraps every line in JSON, which the filter does not read. Send the container's logs to the journal instead. In `docker-compose.yml`, under the Meshloom service:

  ```yaml
  logging:
    driver: journald
    options:
      tag: meshloom
  ```

  Run `docker compose up -d`. The jail then reads Docker's journal entries for your container (its name, as `docker ps` shows it). Naming Docker's own unit as well matters: the container name alone could be forged by another program writing to the journal.

  ```ini
  journalmatch = _SYSTEMD_UNIT=docker.service CONTAINER_NAME=meshloom
  ```
- **Where it blocks.** Traffic to a published port is forwarded to the container, not delivered to the host, so the default ban (the `input` hook) has no effect. Pick one:

  ```ini
  # nftables hosts (the default on current Debian and Fedora)
  banaction = nftables[chain_hook=forward]
  ```

  ```ini
  # or iptables (needs the iptables command; the jail then blocks in Docker's own chain)
  banaction = iptables-multiport
  chain = DOCKER-USER
  ```

  In both, set `port` to the **container's** port, 8000, not the one Docker publishes on the host: by the time the firewall sees the packet, Docker has already rewritten the destination. With `ports: "8080:8000"`, you reach Meshloom on 8080 and write `port = 8000`.

Two more things for Docker:

- Meshloom must see each visitor's own address. In a container without `network_mode: host` and without a declared proxy, it sees the address of Docker's gateway for everyone (`172.x.x.x`) and logs it like any other address: the jail would ban the gateway, that is, every visitor. Always add the Docker networks to `ignoreip`: `ignoreip = 127.0.0.1/8 ::1 172.16.0.0/12` (add `192.168.0.0/16` if your Docker networks use it). [Sign-in delays](/en/docs/deep/access/#sign-in-delays) lists the two ways to make Meshloom see real addresses (host network, or **Client addresses identify clients**); until one of them is in place the jail cannot help.
- Lines marked `proxied=1` are ignored by the filter; they name a reverse proxy on a local network that is not in **Trusted proxies**.

## A reverse proxy on another machine

fail2ban on the Meshloom machine only sees the proxy. Ban on the proxy's machine instead, from the proxy's own access log, since a failed sign-in answers `401` to `POST /api/auth/login`. For nginx with the default `combined` log:

```ini
# /etc/fail2ban/filter.d/meshloom-nginx.conf
[Definition]
failregex = ^<ADDR> \S+ \S+ \[\] "POST /api/auth/login HTTP/[\d.]+" 401\b
```

```ini
# /etc/fail2ban/jail.d/meshloom-nginx.conf
[meshloom-nginx]
enabled = true
filter = meshloom-nginx
logpath = /var/log/nginx/access.log
port = http,https
maxretry = 5
findtime = 10m
bantime = 1h
```

*Not tested end to end.* This only sees the password form; a wrong token or passkey is logged by Meshloom, not by the proxy. If the proxy sits behind another one (a CDN), the address in its log is that one's: configure the real client address in the proxy first. Caddy writes JSON logs: write a filter on its `remote_ip`, `uri` and `status` fields in the same way.

## When it does not help

- **Cloudflare Tunnel.** The connection comes from Cloudflare, not from the visitor: a firewall ban would block Cloudflare for everybody. Use Cloudflare's own rate limiting and Access, and keep this jail off.
- **Home Assistant add-on.** fail2ban cannot be installed in the add-on, and sign-ins through Home Assistant write no line. Home Assistant has its own IP ban.
- **Tailscale or WireGuard only.** Only your own devices connect: nothing to ban.

## Related

- [Sign-in delays, the built-in ban and the `AUTH_FAIL` line](/en/docs/deep/access/#sign-in-delays)
- [Remote access on your own](/en/docs/deep/remote-self-hosted/), with the short pages [Tailscale serve](/en/docs/deep/self-host-tailscale/) and [Meshloom behind your reverse proxy](/en/docs/deep/self-host-proxy/)
- [Security](/en/docs/deep/security/)
