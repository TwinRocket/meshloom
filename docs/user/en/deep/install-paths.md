---
title: Other install paths
description: Docker, systemd, Portainer, and a checkout for development.
level: deep
order: 12
---

The one-line installer from [Installation](/en/docs/install/) covers the common case. This page explains what it does, then covers Docker by hand, Portainer, and running from a copy of the code.

## What the installer does

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

Use `/bin/bash -c` rather than piping into `bash`: the script asks questions and needs your terminal. It asks for a language (English or French), then how Meshloom should run:

- **Install as a background service** (Linux only, recommended). It starts with the machine.
- **Run with Docker.** Meshloom runs in a container.
- **Only open Meshloom in a browser.** Nothing is installed. Choose it if Meshloom already runs on another machine.

You never choose the radio connection here. USB, network or Bluetooth is picked later in the web interface (see [Radio transports](/en/docs/deep/transports/)). The only radio question the installer may ask concerns Docker and USB: if it finds a radio on a USB port, it maps it into the container.

### As a background service

The installer picks the first method that works on your machine:

1. **A signed package from the Meshloom repository** (`apt` or `dnf`), if one exists for your processor.
2. **A package downloaded from the release page**, installed with `apt` or `dnf`.
3. **A copy of the code**: if there is no package for your system, it downloads the latest release into a folder (by default `~/meshloom`) and runs `install_service.sh` from it.

A package installation creates:

- a systemd service called `meshloom`, running as a dedicated `meshloom` user that can use serial and Bluetooth devices;
- the program in `/opt/meshloom`;
- the settings file `/etc/meshloom/meshloom.env`, where bots are **disabled** by default;
- the database and your data in `/var/lib/meshloom`.

Check it with:

```bash
sudo systemctl status meshloom
```

Meshloom listens on port 8000 on every network of the machine.

A copy-of-the-code installation runs as your own user from that folder, and its database is `data/meshcore.db` inside the folder. It does not update itself from the interface: update it by hand (see below).

### With Docker

The installer asks where to keep the Compose file (by default `~/meshloom`), then writes there:

- `docker-compose.yml`, with port 8000, a `./data` folder for the database and, if a USB radio was found, the device mapping;
- `.env`, which holds `MESHLOOM_IMAGE`, the image to run.

An existing `docker-compose.yml` is backed up first. If you edited it, the installer offers to keep your edits.

## Running `install_service.sh` yourself

From a copy of the code:

```bash
bash scripts/setup/install_service.sh
```

It needs `uv` and Python 3.11 or newer. It asks whether to build the interface with Node.js 20 and npm 9 or newer, or to download a ready-made one. It then writes `/etc/systemd/system/meshloom.service`, starts it as your user, and gives the user access to serial and Bluetooth devices.

You can run it again after updating the code: it stops the service, rewrites the unit, reloads systemd and starts it again. It does not set up bots or a password. To set variables, see [Variables and settings](/en/docs/deep/environment/).

To update such an installation:

```bash
cd ~/meshloom
git pull
uv sync
cd frontend && npm install && npm run build && cd ..
sudo systemctl restart meshloom
```

## Docker by hand

The image is `ghcr.io/twinrocket/meshloom`. The repository contains `docker-compose.example.yml`. Its main parts:

- `image: ${MESHLOOM_IMAGE:-ghcr.io/twinrocket/meshloom:latest}`. Without a `.env` file it follows `:latest`. To stay on a given release, write the line `MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:X.Y.Z@sha256:...` in a `.env` file next to it. Every release publishes its digests in a signed `OCI-DIGESTS` file.
- `ports: "8000:8000"`.
- `./data:/app/data`, for the database.
- `devices:`, only to give the container a USB radio. A network or Bluetooth radio is configured in the web interface.
- `MESHCORE_DATABASE_PATH: data/meshcore.db`, and `restart: unless-stopped`.

The container runs as root by default. To run it as user number 10001 instead, set `MESHLOOM_RUN_AS_USER: "10001"`. At startup, the container hands `./data` to that user and gives it the groups of the serial devices. If the radio still cannot be opened that way, Meshloom stays root and writes a warning in its log. Do not use it with Bluetooth. Bluetooth needs more manual setup, described in [Radio transports](/en/docs/deep/transports/).

A Compose file written by hand cannot be updated from the interface. The installer's Docker mode adds a small helper that runs as root on the host: it pins the image by its signed digest in `.env` and installs updates when you ask in **Settings → Updates**. It never edits `docker-compose.yml`. Docker Desktop and rootless Docker get no helper: the stack follows `:latest`.

## Portainer

The repository's [`docker-compose.dev.yaml`](https://github.com/TwinRocket/meshloom/blob/main/docker-compose.dev.yaml) is meant for a stack that **builds the image from the repository**. In Portainer, point the stack at it and load the variables of `.env.example`:

```text
MESHLOOM_HTTP_PORT=8123
MESHLOOM_DATA_PATH=/opt/docker/meshloom/data
MESHCORE_DATABASE_PATH=data/meshcore.db
MESHCORE_DISABLE_BOTS=false
MESHCORE_VAPID_SUBJECT=mailto:you@example.com
# MESHLOOM_COMMUNITY=0
```

The file also publishes port 5001 (`MESHLOOM_PROXY_PORT`), used by the radio proxy once you enable it. If you do not want a build on the server, use `docker-compose.example.yml` instead.

`MESHCORE_VAPID_SUBJECT` is only used when the contact in **Settings → Notifications** is empty. Do not put a real address in a public repository. The radio's address and port are set in the web interface. A new, empty data folder joins Community unless `MESHLOOM_COMMUNITY=0`. See [Meshloom Community](/en/docs/deep/community/).

## From a checkout, for development

```bash
uv sync
uv run uvicorn app.main:app --reload
```

`uv sync` creates the project's own `.venv`. Do not install these dependencies with `apt` or `dnf`. If `uv run uvicorn` stops with `ModuleNotFoundError: No module named 'meshcore'`, start here. See [Troubleshooting](/en/docs/deep/troubleshooting/).

For work on the interface:

```bash
cd frontend
npm install
npm run dev
```

The development server listens on `http://localhost:5173` and passes `/api` calls to port 8000. For production, build the interface once, then start the server:

```bash
cd frontend && npm install && npm run build && cd ..
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The server serves `frontend/dist` if it exists, otherwise `frontend/prebuilt` (a ready-made interface that `scripts/setup/fetch_prebuilt_frontend.py` downloads). If neither exists, it only serves the API. Run the repository's quality checks from its root:

```bash
./scripts/quality/all_quality.sh
```

## Database and updates

| Installation | Database |
|--------------|----------|
| Linux package | `/var/lib/meshloom/meshcore.db` |
| Docker | `./data`, mounted at `/app/data` |
| Copy of the code | `data/meshcore.db` in the folder |

`MESHCORE_DATABASE_PATH` moves it. An update never replaces your database: the structure is upgraded at startup, step by step.

**Settings → Updates** shows the update status and can check for a new version (**Check now**). Where it is supported, it installs the update from a signed source, either on request (**Install now**) or by itself inside a time window and on the days you choose. This works for a Linux package and for a Docker stack managed by the helper. Elsewhere, update by hand:

```bash
sudo apt-get install --only-upgrade meshloom
sudo dnf upgrade meshloom
sudo docker compose pull && sudo docker compose up -d
```

The first line is for Debian, Ubuntu and Raspberry Pi OS, the second for Fedora and similar systems, the third for Docker.

The default address is `http://127.0.0.1:8000`. The page at `/docs` documents the programming interface (API). It is not this website.
