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
| `member` | Everything a `viewer` can, plus send messages, add and edit contacts, channels and contact groups, favourites, pins and muted channels, subscribe its own browser to push notifications, and create API tokens for its own devices and scripts. |
| `viewer` | Read channels and direct messages, mark them read, manage its own profile and sessions. |
| `kiosk` | Read only, for a screen on the wall: channel messages (muted and pending channels included), contacts, channels, packets, statistics and the map. Never a direct message, no settings, no profile. |

What each role sees in the interface follows the table: the screens a role cannot use are hidden or greyed out instead of failing when clicked.

| | `admin` | `member` | `viewer` | `kiosk` |
|---|---|---|---|---|
| Channels and packets | yes | yes | yes | yes (channels only, no direct message) |
| Direct messages | yes | yes | yes | no |
| Composer, new conversation, pin, favourite, mute, delete | yes | yes | no | no |
| Repeater and room panes, mesh test | yes | no | no | no |
| Settings | all sections | Local configuration, Users & Access (own account) | Local configuration, Users & Access (own account) | none |
| Language and theme of this browser | yes | yes | yes | yes, from the account button |

Every role receives the instance's theme, its blocked contacts and names and the region names, whatever it can change: a blocked contact stays hidden for a viewer or a kiosk too. Only administrators read or change the other settings.

An action the server refuses anyway (for example from a page left open before a role change) shows one message, “This account is not allowed to do that.”

The first administrator, and the account Meshloom creates from the Basic auth login of a 4.x installation, are `admin`: an installation with a single administrator works as before.

## Managing accounts

Administrators manage the accounts in **Settings → Users & Access**, or through the API. The page lists every account with its role and where it signs in (local or Home Assistant), and offers, per account: change the role, disable or enable, reset the password (the user is asked for a new one at the next sign-in unless you untick it), sign out everywhere, and delete. You cannot change, disable or delete your own account there, which keeps at least one administrator in place. A kiosk has no password. At the bottom, the role given to a Home Assistant user seen for the first time is set there too; the trusted proxies stay in **Local Configuration**.

Every user (the kiosk excepted) sees **My account** and **My sessions** on the same page: change the display name and password, see the devices signed in (browser, system, address, last seen), revoke one, or sign out everywhere else.

| Request | What it does |
|---|---|
| `GET /api/auth/users` | Lists every account with its role, its origin (`local`, or `ingress` for a Home Assistant user), whether it is disabled and whether it has a password. |
| `POST /api/auth/users` | Creates an account: `{"username", "role", "password", "display_name"}`. Every role but `kiosk` needs an initial password, which the person changes at their first sign-in (send `"must_change_password": false` to skip that). A `kiosk` has no password. |
| `PATCH /api/auth/users/<id>` | Changes the role, the display name, `disabled` (`true` blocks the account) or `must_change_password`. |
| `POST /api/auth/users/<id>/password` | Sets a new password, to change at the next sign-in unless `"must_change_password": false`. |
| `DELETE /api/auth/users/<id>/sessions` | Signs the account out everywhere. |
| `DELETE /api/auth/users/<id>` | Deletes the account. |

- **There is always an administrator.** The last active administrator cannot be demoted, disabled or deleted: the request is refused (`auth_last_admin`). Promote or enable another administrator first.
- **Changes apply at once.** A new role, a disable, a deletion or a password set by an administrator signs the account out everywhere, open pages included, and forgets the browsers it was remembered on. Moving an account to `kiosk` removes its password.
- **A password chosen by an administrator is temporary.** Until the person picks their own, Meshloom lets them do nothing else than change it (or sign out); the request is `PATCH /api/auth/me` with `{"current_password", "new_password"}`. Changing your own password signs your other browsers out. A wrong current password counts like a wrong sign-in, with the same delays (below).
- **Home Assistant users** (see the add-on below) take any role, `kiosk` included, and can be disabled, but never get a password: Home Assistant signs them in. The role given to the next new ones is `ingress_default_role` (`member` by default), set with `PATCH /api/auth/settings`. A deleted Home Assistant user comes back with that role on their next visit, even the one who was made administrator first (only the very first Home Assistant user ever becomes administrator): disable them instead to keep them out.

### When an administrator sets your password

If an administrator sets your password (or `meshloom-admin reset-password --must-change` does), Meshloom shows **Change your password** instead of the app: type the current password, then a new one (at least 12 characters, with the rules shown if it is refused). Nothing else works until it is changed, then the app opens where you were. Other devices of that account are signed out. A kiosk display has no password and never gets this page; if it ever shows, ask an administrator.

## Sign-in delays

Wrong passwords slow the next attempts down; they never lock an account.

- **From one address, for one account:** the first 5 wrong passwords cost nothing. After that, the next attempt waits 1 second, then 2, 4, 8 and so on, up to 1 minute. Meanwhile the sign-in page says how many seconds to wait. Once the wait is over the right password works, and it clears the delay.
- **For one account, from anywhere:** after 5 wrong passwords, the account takes at most one attempt per second, whoever sends it. Someone flooding `admin` gets one try a second, and a browser Meshloom does not know competes with them for that second (see below).
- **From one address, for any account:** past 100 wrong passwords in 15 minutes, that address is refused until its count goes down (one more attempt every 9 seconds).

An IPv6 address counts by its /64 block: one home or server usually holds a whole block and could otherwise change address at each attempt.

**Your own browser always gets in.** After you sign in, Meshloom remembers the browser with a cookie of its own (for 180 days, for the last account that signed in on it). With that cookie, the right password gets in at once, whatever anyone else sends from anywhere. A browser that types the wrong password 5 times loses that pass and follows the rules above; its mistakes are forgotten after 15 minutes without a new one, or at its next successful sign-in. Changing the account's password, role or name, disabling it, or restarting Meshloom forgets the browsers: the next successful sign-in remembers it again.

From a browser Meshloom does not know yet (a new device, a cleared cookie, after a restart):

- **On a Docker install (Compose or a plain container) with no trusted proxy set**, every visitor can arrive from Docker's own address, so the first and last rules are off and only the per-account rule applies. A guesser on the same address competes with you for the one attempt a second: one sending many attempts a second can keep you out until you use a browser Meshloom knows, or list your reverse proxy in **Trusted proxies**.
- **Behind a reverse proxy that is not listed in Trusted proxies**, every visitor counts as the proxy's address, so one guesser's delays apply to everyone behind it. List the proxy. Meshloom logs a warning naming it.
- **Otherwise**, each visitor has their own address and a guesser elsewhere only makes you wait up to a second.

The delays live in memory and start again empty when Meshloom restarts. A delay is written to the audit log once, not for each refused attempt.

## Behind a reverse proxy

Sign-in delays, the audit log and the session list record the address a request comes from. Behind a reverse proxy that is the proxy's, for every visitor, until you list the proxy in **Settings → Local Configuration → Trusted proxies**; Meshloom then uses the visitor's address the proxy forwards. List the proxy's own address only, never a Docker network or its gateway. See [HTTPS, trusted proxies](/en/docs/deep/https/#trusted-proxies). Meshloom also looks at the name in the browser's address: in 5.0.0 an unknown name (your proxy's DNS name, say) is only noted; from 5.1 it will be refused unless declared. See [Host names Meshloom answers to](/en/docs/deep/https/#host-names-meshloom-answers-to).

## Trusted networks

An administrator can let devices on a chosen network in without a login, in **Settings → Users & Access → Trusted networks**: each rule is a network (`192.168.1.50`, `192.168.1.0/24`) and the account those devices act as. The list is empty by default. Use it for a device you control, such as a wall tablet, and give it the smallest network (its own address) and the weakest role that does the job (kiosk or viewer). A sign-in through a rule is written to the audit log once per device and per day.

A rule is **off as soon as a proxy is in the loop**, because behind a proxy or a tunnel every visitor, from anywhere, arrives from the proxy's address:

- a request that came through a reverse proxy or a tunnel (it carries `X-Forwarded-For`, `Forwarded`, `X-Real-IP`, `CF-Connecting-IP` or a similar header), or from an address listed in the trusted proxies, is never signed in by a rule;
- so are requests through Meshloom Remote and Home Assistant ingress (Home Assistant signs those in itself);
- a rule never covers this machine's own loopback (`127.0.0.1`, `::1`), except inside the Home Assistant add-on: a local proxy or an `ssh -L` tunnel would bring every visitor from there;
- the address in the browser must be an IP address, `localhost`, a local name (`.local`, `.home.arpa`, `.lan`, `.internal`), this machine's name or a public address you declared, so a web page cannot use a name of its own to reach Meshloom as that account.

Meshloom refuses to save a rule that overlaps a trusted proxy, one that covers loopback outside the add-on, and, while a public address is configured, one that covers the Docker gateway. Settings warns in red when a rule signs in an **administrator**: anyone on that network can then change every setting, account and plugin. The **Access security notice** at the top of **Users & Access** says when a rule signs in an administrator while plugins run with reduced isolation, or covers loopback or the Docker gateway while a public address is configured.

## API and device tokens

A script, a Home Assistant sensor or a phone app talks to Meshloom with a **token** instead of a password: one per device, so you can revoke one without touching the others. Members and administrators create their own; a viewer or a kiosk cannot. There is no page for them yet: use the API while signed in.

| Request | What it does |
|---|---|
| `POST /api/auth/tokens` | Creates a token: `{"name": "ha-sensor", "scopes": ["read"]}`, optionally `"expires_at"` (a Unix time). The answer holds the token (`mlt_…`) **once**: copy it then, Meshloom keeps only a fingerprint of it and cannot show it again. |
| `GET /api/auth/tokens` | Lists your tokens (an administrator sees everyone's): name, scopes, creation, last use, expiry. Never the token itself. |
| `DELETE /api/auth/tokens/<id>` | Revokes a token. It stops working at once, and a connection open with it is closed. |

The device sends the token in a header, `Authorization: Bearer mlt_…`, on every request and when it opens the live connection (`/api/ws`). A token put in the address (`?token=`) is ignored. It does not work through Home Assistant's ingress: point the device at Meshloom's own address.

**Scopes** say what a token may do: `read` (channels, contacts, packets, map), `read_private` (direct messages), `send` (send and edit contacts and channels), `repeater_admin` (repeaters and rooms) and `admin` (everything). A token never gets more than its account's role: a member cannot ask for `repeater_admin` or `admin` (`auth_token_scope`), and if an account loses a role later, its tokens lose it too. A disabled or deleted account takes its tokens with it. Give each token the fewest scopes it needs: a dashboard needs only `read`.

## Audit log

Meshloom keeps a log of sensitive actions: sign-ins (successful or failed), sign-outs and sessions signed out, accounts created, renamed, given another role, disabled, enabled again or deleted (refusals included, such as an attempt to demote the last administrator), password changes, the first administrator setup, changes to the access settings, tokens created and revoked and uses of a revoked or expired token, bots created, changed, deleted or stopped, backup restores, private key imports, radio factory resets and updates started. Each `meshloom-admin` command below writes an entry too, refused ones included.

Each entry holds the time, the account that acted (none for a failed sign-in or the command line), the address it came from, the way in (local network, Home Assistant, command line, or Meshloom itself for an automatic update), what was acted on and how it ended. It never holds a password, an installation code, a session, a key or a bot's code. A failed sign-in names the account only when the typed name matches one; any other typed name is recorded as unknown, so a password typed into the name field is not kept.

Administrators read it in **Settings → Users & Access → Audit log** (hidden from everyone else), or through `GET /api/auth/audit`: newest first, by pages, with a button to load older entries. The filters (action family, action, outcome, account, dates) narrow the entries already loaded; load older ones to look further back. Entries are kept **366 days**, then deleted by an hourly clean-up; an administrator changes that with `PATCH /api/auth/settings` and `{"audit_retention_days": <1 to 36500>}`. The log is not in the JSON backups; the database download (**Settings → Database**) keeps it.

## Backup and restore

The full backup (**Settings → Database**) carries the accounts when **Include accounts** is checked, which it is by default: their roles, password hashes and two-factor secrets, and the API tokens (only their fingerprints, so devices keep working after a restore), never the sessions or the audit log. Restoring it on a new machine brings the same accounts and passwords back. A restore replaces the accounts and signs everyone out; it never leaves Meshloom without an enabled administrator who can sign in here (a password account, or a Home Assistant user on the add-on): a file without one, or without accounts at all, keeps the current accounts. Details on the [backup page](/en/docs/backup/#what-the-backup-contains). The database download keeps the accounts but not their passwords: after putting it back, reset a password with `meshloom-admin` (below).

## Commands

| Command | What it does |
|---|---|
| `meshloom-admin reset-password <user>` | Sets a new password (asked twice, or one line on standard input with `--stdin`) and signs the user out everywhere. `--must-change` asks for another at the next sign-in. Refused for a `kiosk` or Home Assistant account, which has no password. |
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

The code has 8 digits; Meshloom also prints a new one in its log at each start while no account exists. Spaces or dashes typed inside it are ignored. A fresh installation asks for no code. If Meshloom could not prepare the first start (the log says so), creating the administrator is refused until it can; restart Meshloom if it lasts. A code printed by `meshloom-admin setup-code` in the meantime stays valid: Meshloom does not replace it when it tries again.

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

Through the sidebar, Home Assistant signs you in: Meshloom accepts the Home Assistant user who opens it, with no password of its own. The first Home Assistant user to open Meshloom becomes its administrator, even if a Meshloom administrator already exists (from the 4.18 `basic_auth_*` options or `meshloom-admin`), so the sidebar always reaches an administrator; the next ones get the **Member** role (an administrator can change it, and each user's role: see "Managing accounts" above). Only Home Assistant administrators see the Meshloom panel, so these are people who already administer your Home Assistant. If you cannot open Meshloom, fix your access in Home Assistant itself. If you gave the add-on's port 8000 a host port, that address asks for a Meshloom account like any other install. The command still exists for the rare case where you need it. It runs in the add-on's container, so it needs a way to reach the host's Docker: the *Advanced SSH & Web Terminal* add-on with **Protection mode** turned off.

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
