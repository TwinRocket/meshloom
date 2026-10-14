# Advanced Setup And Troubleshooting

This page collects what goes beyond a normal install: environment variables, fixes for radios that behave oddly, HTTPS, reverse proxies and debug logs. Once the backend is running, FastAPI serves interactive API docs at `/docs` on the same host and port as the web UI. For a default local launch, that is http://localhost:8000/docs.

## Where to set an environment variable

A variable is read when Meshloom starts, so restart it after changing one.

| Install | Where |
|---|---|
| Package (`.deb` / `.rpm`) | `/etc/meshloom/meshloom.env`, then `sudo systemctl restart meshloom` |
| Docker Compose | the `environment:` block of your `docker-compose.yml`, then `docker compose up -d` |
| Checkout installed with `install_service.sh` | `Environment=` lines in the `meshloom` unit |
| Home Assistant add-on | the add-on options, see [`meshloom/DOCS.md`](meshloom/DOCS.md) |

## Everyday environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHCORE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `MESHCORE_DATABASE_PATH` | `data/meshcore.db` | The SQLite file. The package sets `/var/lib/meshloom/meshcore.db` |
| `MESHCORE_DISABLE_BOTS` | false | Turns the bot system off. Bots run Python code with full access to the machine; the package turns them off by default |
| `MESHCORE_BASIC_AUTH_USERNAME` / `MESHCORE_BASIC_AUTH_PASSWORD` | empty | Asks for a username and password on everything, including the WebSocket. Both or neither, otherwise Meshloom refuses to start. Use HTTPS with it |
| `MESHLOOM_RUN_AS_USER` | unset | Docker only: a numeric user id (the installer uses `10001`). Meshloom then runs as that user instead of root, after giving it `./data` and the serial devices you mapped. If the radio is still unreadable it stays root and logs a warning. Not for Bluetooth, which needs root on the host |
| `MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT` | false | See [Private Key Export](#private-key-export) |

The radio itself (USB, network or Bluetooth) is never chosen by a variable: it is set in **Settings > Radio**.

### Retired variables

These variables no longer change anything. Their settings live in Meshloom's database and are edited in the interface:

| Variable | Now in |
|----------|--------|
| `MESHCORE_VAPID_SUBJECT` | **Settings > Notifications**, VAPID subject field. Apple refuses `.local`, so set a real `mailto:` for iPhones |
| `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK` | **Settings > Radio > Advanced**, "Check for messages every 10 seconds" |
| `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE` | **Settings > Radio > Advanced**, "Rewrite the channel before every send" |
| `MESHCORE_LOAD_WITH_AUTOEVICT` | **Settings > Radio > Advanced**, "Let the radio evict its oldest contacts" |

At the first start of a version that retired them, Meshloom copies a value still set into its database once: the VAPID subject when the field in Settings is empty, a workaround when the variable is `true`. After that the variables are never read again, and each start logs a warning while one is still set: remove it from your environment.

Meshloom Community is not set through the environment: it is an opt-in in **Settings > Meshloom Community** (a new database starts with it off), and its servers are fixed. `MESHLOOM_COMMUNITY`, `MESHLOOM_COMMUNITY_IATA`, `MESHLOOM_COMMUNITY_BROKER_HOST` and `MESHLOOM_COMMUNITY_API_BASE` are no longer read: an existing install that still sets `MESHLOOM_COMMUNITY_IATA` has its code copied once into the database at the first start after the upgrade, and each start logs a warning while any of them is set. See [Variables and settings](docs/user/en/deep/environment.md).

## Remediation & Advanced Environment Variables

These are intended for diagnosing or working around radios that behave oddly, or enabling advanced functionality.

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT` | false | Enable `GET /api/radio/private-key` to return the in-memory private key as hex for backup or migration. Only enable on a trusted network. Import via `PUT /api/radio/private-key` is always available. ([docs](#private-key-export)) |


By default the app relies on radio events plus MeshCore auto-fetch for incoming messages, and also runs a low-frequency hourly audit poll. That audit checks both:

- whether messages were left on the radio without reaching the app through event subscription
- whether the app's channel-slot expectations still match the radio's actual channel listing

If the audit finds a mismatch, you'll see an error in the application UI and your logs. The workarounds below are switched on in **Settings > Radio > Advanced** (they used to be the `MESHCORE_*` variables listed under [Retired variables](#retired-variables)).

### Message Poll Fallback

If you see that warning, or if messages on the radio never show up in the app, turn on **Check for messages every 10 seconds** to switch that task into a more aggressive 10-second safety net (`get_msg()` polling). It applies immediately.

### Force Channel Slot Reconfigure

If room sends appear to be using the wrong channel slot or another client is changing slots underneath this app, turn on **Rewrite the channel before every send**: channel-slot reuse is disabled and `set_channel(...)` runs before every channel send (sending is a little slower, because the radio is written to before each send). It applies immediately. TCP connections always behave this way.

### Private Key Export

`MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT=true` enables `GET /api/radio/private-key`, which returns the in-memory private key as hex for backup or migration. The key is held in memory only (exported from the radio on connect) and is never persisted to disk. Only enable this on a trusted network when you need to retrieve the key.

Import via `PUT /api/radio/private-key` is always available regardless of this setting — it is write-only and does not expose key material.

The Radio Settings config export/import feature uses these endpoints. When export is disabled, config exports will omit the private key and show a notice.

## ModuleNotFoundError: No module named 'meshcore'

This means uvicorn ran against your system Python instead of the project's virtualenv. Fix it from the repo root:

- Run `uv sync`. It is required before `uv run ...` and creates the `.venv` that holds the dependencies. Do not try to install them with `apt`/`dnf` — most are not packaged, and they are not needed on the system Python at all.
- Check `uv --version`. If that fails, uv isn't installed (or something else named `uv` is on your `PATH`): install it with `curl -LsSf https://astral.sh/uv/install.sh | sh` and restart your shell.
- Check `which uvicorn`. If it resolves to `/usr/bin/uvicorn`, you are picking up a system uvicorn; always launch through `uv run uvicorn ...` from the repo root so the project venv wins.

## Contact Loading Issues

Meshloom loads favorite and recently active contacts onto the radio so that the radio can automatically acknowledge incoming DMs on your behalf. To do this, it first enumerates the radio's existing contact table, then reconciles it with the desired working set.

On BLE connections with many contacts (or radios with large contact tables from organic advertisements), the initial contact enumeration may time out. If this happens, the app will still attempt to load your favorites and recent contacts onto the radio on a best-effort basis, but without a full snapshot of what's already on the radio, some adds may be redundant or fail.

If the radio's contact table is already full (from contacts added by advertisements or another client), the app may not be able to load all desired contacts. In this case you'll see a warning that auto-DM acking may not work for all contacts. To resolve this:

- **Clear the radio's contact table** using another MeshCore client (e.g., the official companion app), then restart Meshloom
- **Lower "Max Contacts on Radio"** in **Settings > Radio**: the app fills to about 80% of it
- **Turn on autoevict mode** (see below) to let the radio automatically make room
- If you don't need auto-DM acking, you can safely ignore these warnings — **sending and receiving messages is never affected**

### Autoevict Mode

**Settings > Radio > Advanced > Let the radio evict its oldest contacts** enables an alternative contact loading strategy that avoids TABLE_FULL errors entirely. It applies at the next connection; turning it off later does not clear the radio's preference (clear it with another MeshCore app). On connect, the app enables the radio's `AUTO_ADD_OVERWRITE_OLDEST` preference, which makes the radio automatically evict the oldest non-favorite contact when the contact table is full. This means:

- Contact adds never fail — the radio always makes room by evicting stale contacts
- The app can load contacts even when it can't enumerate the radio's existing contact table (e.g., on slow BLE connections)
- No contact removal step is needed during reconciliation

**Trade-off:** Contacts loaded by the app are not marked as radio-side favorites, so they are eviction candidates if the radio receives a new advertisement while full. In practice, freshly-loaded contacts have a recent `lastmod` timestamp and will be among the last to be evicted. If you disconnect the radio from Meshloom and use it standalone, your contacts will not be protected from eviction by newer advertisements.

## Sub-Path Reverse Proxy

Meshloom works behind a reverse proxy that serves it under a sub-path (e.g. `/meshcore/` or Home Assistant ingress). All frontend asset and API paths are relative, and the web manifest uses relative `start_url` / `scope` (`./`), so they resolve under any prefix. No `X-Forwarded-*` header is needed.

The proxy must ensure the sub-path URL has a **trailing slash**. If a user visits `/meshcore` (no slash), relative paths break. For Nginx, a `location /meshcore/ { ... }` block (note the trailing slash) does the right thing.

CORS currently allows every origin with credentials (`allow_origins=["*"]`, `allow_credentials=True`). Do not expose the interface beyond a trusted network on the strength of Basic auth alone.

## HTTPS

WebGPU channel-finding requires a secure context when you are not on `localhost`.

This applies to a checkout you start yourself. A package install runs `/opt/meshloom/.venv/bin/uvicorn` from the `meshloom` systemd unit without TLS, so for HTTPS there put a reverse proxy in front of port 8000.

Generate a local cert and start the backend with TLS:

```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=key.pem --ssl-certfile=cert.pem
```

For Docker Compose, generate the cert, mount it into the container, and override the launch command:

```yaml
services:
  meshloom:
    volumes:
      - ./data:/app/data
      - ./cert.pem:/app/cert.pem:ro
      - ./key.pem:/app/key.pem:ro
    command: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=/app/key.pem --ssl-certfile=/app/cert.pem
```

Accept the browser warning, or use [mkcert](https://github.com/FiloSottile/mkcert) for locally-trusted certs.

## Portainer GitOps

For a stack that **follows `main`** (not the releases), use [`docker-compose.dev.yaml`](docker-compose.dev.yaml) as the Compose path and load the keys from [`.env.example`](.env.example) into the Portainer Environment section (or a local `.env`). Besides the `MESHCORE_*` variables, the file reads `MESHLOOM_HTTP_PORT` (web port on the host, default 8000), `MESHLOOM_PROXY_PORT` (radio proxy port on the host, default 5001), `MESHLOOM_DATA_PATH` (host folder for the database, default `/app/data` on the host: set it) and `MESHLOOM_NETWORK` (Docker network name). Do not commit real radio hosts, ports, or VAPID addresses.

The file runs `ghcr.io/twinrocket/meshloom:edge`, the image CI publishes for every commit on `main` (`docker.yml`), with `pull_policy: always`; set `MESHLOOM_IMAGE` to run another tag or a pinned digest. The host builds nothing. It used to build the image from the copied tree, but Portainer updates a stack by copying each new commit over the previous files and **never deletes a file removed from the repository**, and a leftover could break the build. For example, the old `useCommunityLeave.ts` shadowed the new `useCommunityLeave.tsx`, and `tsc` then failed with `npm run build` exit code 2 while CI was green.

`:edge` is published a few minutes after the commit lands, so a redeploy triggered by the commit alone can still pull the previous image. In the stack's GitOps settings, turn on **Re-pull image** and **Force redeployment** so each check pulls the latest `:edge`; the container is recreated only when the image changed.

The maintainers' own stacks do not poll. Their Portainer webhooks are stored in the repository secret `EDGE_DEPLOY_WEBHOOKS`, one URL per line. The `Deploy edge` job of `docker.yml` calls them once the new `:edge` manifest is pushed, so the redeploy never runs ahead of the image. Set those stacks' GitOps update mechanism to **Webhook** instead of polling. A failed call only raises a warning on the run, and the stack keeps the previous image.

A stack created before this change that is stuck on that build error picks up the new file at its next update. If it still fails, delete the stack and create it again with the same variables and data folder (the database is not touched).

To run the **published image** instead, start from [`docker-compose.example.yml`](docker-compose.example.yml) and pin `MESHLOOM_IMAGE` (see the Update section of the [README](README.md)).

## Systemd Service

On Linux, prefer the public one-liner in [README.md](README.md) (`install.sh`). That path installs the `meshloom` apt/dnf package when available (unit + `/etc/meshloom/meshloom.env` + `/var/lib/meshloom`), or falls back to a source checkout.

From an existing clone, you can still run the checkout installer. It runs as your current user from the repo directory. The unit name is `meshloom`.

```bash
bash scripts/setup/install_service.sh
```

It asks only how to get the frontend (build locally with Node.js 20+ and npm 9+, or download the prebuilt one); the radio transport is chosen in the web UI. Re-running it stops the service, rewrites the unit file, reloads systemd, and starts it again. Set other environment variables (bots, Basic auth) as `Environment=` lines in the unit.

A package install is different: the unit is `meshloom.service`, runs as the `meshloom` user, keeps its data in `/var/lib/meshloom` and reads `/etc/meshloom/meshloom.env`.

## Debug Logging And Bug Reports

If you're experiencing issues or opening a bug report, please start the backend with debug logging enabled. Debug mode provides a much more detailed breakdown of radio communication, packet processing, and other internal operations, which makes it significantly easier to diagnose problems.

```bash
MESHCORE_LOG_LEVEL=DEBUG uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

On a package install, add `MESHCORE_LOG_LEVEL=DEBUG` to `/etc/meshloom/meshloom.env`, restart the service and read the logs with `journalctl -u meshloom`. With Docker, add it to `environment:` and use `docker compose logs meshloom`. The add-on has a `log_level` option.

You can also navigate to `/api/debug` (or go to Settings -> About -> "Open debug support snapshot" at the bottom). This debug block describes the operating environment, the radio's status and settings, and whether the channels and contacts on the radio match what Meshloom expects. It ends with the most recent log lines (up to 1000). **Above the logs there are no channel names and no secret keys: contacts and channel keys appear only as SHA-256 hashes. It does show how the radio is connected (serial port, TCP host or Bluetooth address), the radio's own name and public key, and the name and last error of any integration that is failing. The logs may contain channel names or keys (but never your private key).** If you do not want to share the logs, copy only up to the `STOP COPYING HERE` marker.

## Development Notes

For day-to-day development, see [CONTRIBUTING.md](CONTRIBUTING.md).
