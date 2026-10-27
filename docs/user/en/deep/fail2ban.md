---
title: Blocking repeated sign-in failures with fail2ban
description: Turn on the fail2ban jail shipped with the Linux packages, on Docker, or on the reverse proxy in front of Meshloom.
level: deep
order: 24.5
---

Meshloom already slows down someone who keeps guessing a password ([Sign-in delays](/en/docs/deep/access/#sign-in-delays)). [fail2ban](https://github.com/fail2ban/fail2ban) adds a block at the firewall: an address that fails five times in ten minutes can no longer reach port 8000 at all, for an hour, then longer each time it comes back. It is optional, and off until you turn it on.

Each refused password, code, passkey, token or installation code writes one line starting with `AUTH_FAIL` to Meshloom's log, with the address it came from. fail2ban watches those lines. Nothing in them identifies the account: only a short code that changes at each restart.

## What the Linux packages install

The `.deb` and `.rpm` packages carry two files, both kept when you upgrade (an edit of yours is never overwritten):

- `/etc/fail2ban/filter.d/meshloom.conf`: how to read the line.
- `/etc/fail2ban/jail.d/meshloom.conf`: the jail, **disabled** (`enabled = false`). It reads the systemd journal of `meshloom.service`, bans after 5 failures in 10 minutes for 1 hour, doubles the ban each time the address returns (at most a week), and covers port 8000.

fail2ban itself is only suggested: Meshloom works without it, and installing the package does not install it.

## Turn it on (Linux package)

1. Install fail2ban: `sudo apt install fail2ban` or `sudo dnf install fail2ban` (on Fedora and RHEL it comes from EPEL).
2. Edit `/etc/fail2ban/jail.d/meshloom.conf` and change `enabled = false` to `enabled = true`. If you changed the port with `--port` in the service, change `port = 8000` as well.
3. `sudo fail2ban-client reload`, then `sudo fail2ban-client status meshloom` shows the jail.
4. To test, fail a sign-in 5 times from another computer, then look at `sudo fail2ban-client status meshloom`: its address is in the list. `sudo fail2ban-client set meshloom unbanip <address>` lifts the ban.

To never ban an address of yours, add `ignoreip = 127.0.0.1/8 ::1 192.168.1.0/24` under `[meshloom]` (your own network).

## Behind a reverse proxy on the same machine

List the proxy in [Trusted proxies](/en/docs/deep/https/#trusted-proxies) first. Until you do, every visitor looks like the proxy: Meshloom marks the line `proxied=1` and the filter **ignores it**, because banning your own proxy would lock everyone out. Once the proxy is declared, the line carries the visitor's address. A firewall ban on this machine then blocks the proxy's traffic from that visitor's address only if the proxy forwards the visitor's own address, which a normal reverse proxy does not: the firewall sees the proxy as the source. To block at the proxy, use the next section.

## Docker

The container logs to its standard error, so the jail changes in two places: where it reads and where it blocks.

- **Where it reads.** Docker's default `json-file` driver wraps every line in JSON, which the filter does not read. Send the container's logs to the journal instead. In `docker-compose.yml`, under the Meshloom service:

  ```yaml
  logging:
    driver: journald
    options:
      tag: meshloom
  ```

  Run `docker compose up -d`, then set `journalmatch = CONTAINER_NAME=meshloom` in the jail (your container's name, as `docker ps` shows it) in place of `_SYSTEMD_UNIT=meshloom.service`.
- **Where it blocks.** Docker publishes a port before the usual firewall chains see the traffic, so the default ban would have no effect. Add to the jail:

  ```ini
  chain = DOCKER-USER
  port = 8000
  ```

  with `port` set to the port Docker publishes.

Meshloom must also see each visitor's own address. In a container without `network_mode: host` and without a declared proxy, it sees Docker's address for everyone and marks the line `proxied=1`: nothing is banned. [Sign-in delays](/en/docs/deep/access/#sign-in-delays) lists the two ways out (host network, or **Client addresses identify clients**).

## A reverse proxy on another machine

fail2ban on the Meshloom machine only sees the proxy. Ban on the proxy's machine instead, from the proxy's own access log, since a failed sign-in answers `401` to `POST /api/auth/login`. For nginx with the default `combined` log:

```ini
# /etc/fail2ban/filter.d/meshloom-nginx.conf
[Definition]
failregex = ^<ADDR> \S+ \S+ \[[^\]]+\] "POST /api/auth/login HTTP/[\d.]+" 401\b
datepattern = {^LN-BEG}%%d/%%b/%%Y:%%H:%%M:%%S %%z
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

- [Sign-in delays and the `AUTH_FAIL` line](/en/docs/deep/access/#sign-in-delays)
- [Remote access on your own](/en/docs/deep/remote-self-hosted/)
- [Security](/en/docs/deep/security/)
