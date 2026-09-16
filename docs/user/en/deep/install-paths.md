---
title: Other install paths
description: Docker, systemd, Portainer, and a checkout for development.
level: deep
order: 12
---

The [Installer](/en/docs/install/) one-liner covers the common case. This page explains it, plus Docker, Portainer, and a repository checkout.

## What the one-liner does

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

`/bin/bash -c` matters because the script asks questions. On Linux it offers native systemd or Docker. Docker may ask USB versus network only to emit a Compose `devices:` mapping. Radio transport itself is configured in the web UI.

Native systemd installation uses `apt-get` or `dnf` when a package is available. It installs:

- the `meshloom` systemd unit
- `/etc/meshloom/meshloom.env`
- `/var/lib/meshloom`

Check it normally:

```bash
sudo systemctl status meshloom
```

## `install_service.sh` from a clone

From an existing checkout:

```bash
bash scripts/setup/install_service.sh
```

The script is repeatable. Run it again to change the bot setting or authentication credentials. It stops the service, rewrites the unit, reloads systemd, and starts it with the new configuration. Radio transport is configured in the web UI, not in the unit file.

## Docker

The image is `ghcr.io/twinrocket/meshloom`. The repository includes `docker-compose.example.yml`. Important parts are:

- `./data:/app/data` for the SQLite database
- an optional radio `devices:` mapping for USB (TCP and BLE are configured in the UI)
- `MESHCORE_DATABASE_PATH: data/meshcore.db`
- `restart: unless-stopped`

The example also shows `user: "${UID:-1000}:${GID:-1000}"` to avoid root-owned files. This can complicate serial access and may require an extra group such as `dialout`. BLE needs further manual changes; see [Radio transports](/en/docs/deep/transports/).

## Portainer

For a stack built from the repository, use [`docker-compose.dev.yaml`](https://github.com/TwinRocket/meshloom/blob/main/docker-compose.dev.yaml) and load `.env.example` values in Portainer:

```text
MESHLOOM_HTTP_PORT=8123
MESHLOOM_DATA_PATH=/opt/docker/meshloom/data
MESHCORE_DATABASE_PATH=data/meshcore.db
MESHCORE_DISABLE_BOTS=false
MESHCORE_VAPID_SUBJECT=mailto:you@example.com
# MESHLOOM_COMMUNITY=0
```

`MESHCORE_VAPID_SUBJECT` is only the fallback when the Settings → Notifications VAPID subject is empty. Do not commit real VAPID addresses. Radio host and port are set in the web UI. A new empty data volume joins Community unless `MESHLOOM_COMMUNITY=0`. See [Meshloom Community](/en/docs/deep/community/).

## From a checkout

```bash
uv sync
uv run uvicorn app.main:app --reload
```

`uv sync` creates the project `.venv`; do not install these dependencies with `apt` or `dnf`. If `uv run uvicorn` reports `ModuleNotFoundError: No module named 'meshcore'`, start here. See [Troubleshooting](/en/docs/deep/troubleshooting/).

For frontend development:

```bash
cd frontend
npm install
npm run dev
```

Vite listens on `http://localhost:5173` and proxies `/api` to port 8000. For production:

```bash
cd frontend && npm install && npm run build && cd ..
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

If `frontend/dist` is absent, the backend checks `frontend/prebuilt`. If neither exists, it serves the API without static frontend routes. Run the repository quality checks from its root:

```bash
./scripts/quality/all_quality.sh
```

## Database and updates

| Installation | Database location |
|--------------|-------------------|
| systemd package | `/var/lib/meshloom` |
| Docker | `./data` (mounted at `/app/data`) |
| Checkout | `data/meshcore.db`, relative to the repository |

`MESHCORE_DATABASE_PATH` moves the database. Updates do not replace it; SQLite migrations run at startup in `user_version` order.

```bash
sudo apt upgrade
sudo dnf upgrade
sudo docker compose pull && sudo docker compose up -d
```

The default UI is `http://127.0.0.1:8000`. FastAPI’s generated API documentation is at `/docs`; it is not this site.
