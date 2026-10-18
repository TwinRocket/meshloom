---
title: Recover access
description: meshloom-admin, the command that resets a password, creates an administrator or issues the installation code from the machine, for each way of installing Meshloom.
level: deep
order: 21
---

From Meshloom 5.0, signing in needs a user account (see [Security](/en/docs/deep/security/)). If you lose the administrator's password, you do not need the web interface to get back in: `meshloom-admin` works on the machine that holds the database. Whoever can run it there already controls Meshloom's data, so it asks for no login.

It changes the database file directly. A running Meshloom sees the change on its next request: no restart.

## Roles

Each account has one of four roles. The server checks the role on every request, whatever the interface shows: a request outside the role is refused (`auth_forbidden`).

| Role | Can |
|---|---|
| `admin` | Everything: settings, radio, repeaters and rooms, integrations and bots, backup and restore, the private key, updates, Community, notifications, the debug report. |
| `member` | Everything a `viewer` can, plus send messages, add and edit contacts, channels and contact groups, favourites, pins and muted channels, and subscribe its own browser to push notifications. |
| `viewer` | Read channels and direct messages, mark them read, manage its own profile and sessions. |
| `kiosk` | Read only, for a screen on the wall: channel messages (muted and pending channels included), contacts, channels, packets, statistics and the map. Never a direct message, no settings, no profile. |

The first administrator, and the account Meshloom creates from the Basic auth login of a 4.x installation, are `admin`: an installation with a single administrator works as before.

## Behind a reverse proxy

Sign-in delays, the audit log and the session list record the address a request comes from. Behind a reverse proxy that is the proxy's, for every visitor, until you list the proxy in **Settings → Local Configuration → Trusted proxies**; Meshloom then uses the visitor's address the proxy forwards. See [HTTPS, trusted proxies](/en/docs/deep/https/#trusted-proxies).

## Commands

| Command | What it does |
|---|---|
| `meshloom-admin reset-password <user>` | Sets a new password (asked twice, or one line on standard input with `--stdin`) and signs the user out everywhere. `--must-change` asks for another at the next sign-in. |
| `meshloom-admin create-admin <user>` | Creates an administrator. Same password options, plus `--display-name`. |
| `meshloom-admin setup-code` | Issues a new installation code and prints it (see below). |
| `meshloom-admin list-users` | Lists the accounts: id, role, provider, active or disabled, whether a password is set. `--json` for scripts. |
| `meshloom-admin disable-user <user>` | Blocks a user and signs it out. Refuses the last active administrator unless you add `--force`. |
| `meshloom-admin enable-user <user>` | Lets a disabled user sign in again. |
| `meshloom-admin revoke-sessions <user>` or `--all` | Signs one user, or everyone, out. |

A running Meshloom sees these changes on its next request; a browser that stays open on a page signed out this way loses its live connection within 30 seconds.

Passwords are at least 12 characters and are never taken from the command line, so they stay out of your shell history. `meshloom-admin --help` and `meshloom-admin <command> --help` list every option. A command exits with 0 when it worked and 1 with a message when it did not, for example when the database file is missing, unreadable or not yet upgraded (start Meshloom once on the new version first).

### The installation code

When Meshloom is updated from an older version, the first administrator must prove they have access to the machine by typing an installation code. Meshloom stores only a fingerprint of the code, so it cannot be shown again: `meshloom-admin setup-code` makes a **new** one and the previous code stops working. The command only works while no account exists; afterwards use `create-admin` or `reset-password`.

The code has 8 digits; Meshloom also prints a new one in its log at each start while no account exists. Spaces or dashes typed inside it are ignored. A fresh installation asks for no code. If Meshloom could not prepare the first start (the log says so), creating the administrator is refused until it can; restart Meshloom if it lasts.

## The command for each installation

Replace `alice` with the account name.

### Linux package (apt or dnf)

Run it with `sudo`:

```bash
sudo meshloom-admin reset-password alice
```

The command refuses to run without root. As root it reads the database path from `/etc/meshloom/meshloom.env` (a file only root can read), then switches to the `meshloom` user, which owns the database, so SQLite never leaves files the service cannot write. The same command is `/opt/meshloom/.venv/bin/meshloom-admin`.

### Docker and Docker Compose

Run it inside the container, from the folder holding `docker-compose.yml`:

```bash
docker compose exec meshloom meshloom-admin reset-password alice
```

With `docker run`, use `docker exec -it <container> meshloom-admin reset-password alice`. If the container runs as user 10001 (`MESHLOOM_RUN_AS_USER`), add `--user 10001` to `exec`, so the database files keep their owner. To feed a script, read the password without echoing it and pass it on standard input, so it never appears in your shell history: `read -rs pw; printf '%s\n' "$pw" | docker compose exec -T meshloom meshloom-admin reset-password alice --stdin`.

### Home Assistant add-on

Through the sidebar, Home Assistant signs you in: Meshloom accepts the Home Assistant user who opens it, with no password of its own. The first Home Assistant user to open Meshloom becomes its administrator, even if a Meshloom administrator already exists (from the 4.18 `basic_auth_*` options or `meshloom-admin`), so the sidebar always reaches an administrator; the next ones get the **Member** role. Only Home Assistant administrators see the Meshloom panel, so these are people who already administer your Home Assistant. If you cannot open Meshloom, fix your access in Home Assistant itself. If you gave the add-on's port 8000 a host port, that address asks for a Meshloom account like any other install. The command still exists for the rare case where you need it. It runs in the add-on's container, so it needs a way to reach the host's Docker: the *Advanced SSH & Web Terminal* add-on with **Protection mode** turned off.

```bash
docker ps --format '{{.Names}}' | grep meshloom
docker exec -it addon_xxxxxxxx_meshloom meshloom-admin list-users
```

Use the name printed by the first command. The add-on's database is `/config/meshcore.db` inside the container, which the command finds by itself.

### Raspberry Pi image

The image installs the Linux package, so the command is the same. Open a shell on the Pi over SSH (the user and key you set in Raspberry Pi Imager), then:

```bash
sudo meshloom-admin reset-password alice
```

If you need the installation code and cannot see the log, `meshloom-admin setup-code` gives a new one over SSH.

### Copy of the code

From the Meshloom folder, as the user that runs it:

```bash
cd ~/meshloom
uv run python -m app.cli reset-password alice
```

The database is `data/meshcore.db` in that folder.

## If it does not start

- **`database not found`**: the command looked at the wrong file. Give it the path with `--db /path/to/meshcore.db`, or set `MESHCORE_DATABASE_PATH`.
- **`run it as root`** (package): put `sudo` in front.
- **`cannot read /etc/meshloom/meshloom.env`**: pass the database with `--db /path/to/meshcore.db` or set `MESHCORE_DATABASE_PATH`.
- **`cannot read database` or `cannot open database for writing`**: you are not the user that owns the file. Use `sudo meshloom-admin` (package) or `docker compose exec` (Docker).
- **`start Meshloom once`**: the database is older than the program. Start Meshloom so it upgrades the database, then run the command again.
