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

You never choose the radio connection here. USB, network or Bluetooth is picked later in the web interface (see [Radio transports](/en/docs/deep/transports/)). The only radio question the installer may ask concerns Docker and USB: if it finds one serial device, it maps it into the container; if it finds several, it asks which one.

### As a background service

The installer picks the first method that works on your machine:

1. **A signed package from the Meshloom repository** (`apt` or `dnf`), if one exists for your processor.
2. **A package downloaded from the release page**, installed with `apt` or `dnf`.
3. **A copy of the code**: if there is no package for your system, it copies the code of the latest release with `git` into a folder (by default `~/meshloom`) and runs `install_service.sh` from it.

A package installation creates:

- a systemd service called `meshloom`, running as a dedicated `meshloom` user that can use serial and Bluetooth devices;
- the program in `/opt/meshloom`, which the service can read but not change;
- the settings file `/etc/meshloom/meshloom.env`, where `MESHCORE_DISABLE_PLUGINS=true` (commented out by default) locks every extension;
- the database and your data in `/var/lib/meshloom`.

The package also recommends bubblewrap, which keeps plugins in a sandbox (see [Plugins](/en/docs/deep/plugins/)), and the service may run at most 512 processes and threads in total, plugins included.

Check it with:

```bash
sudo systemctl status meshloom
```

Meshloom listens on port 8000 on every network of the machine.

A copy-of-the-code installation runs as your own user from that folder, and its database is `data/meshcore.db` inside the folder. It does not update itself from the interface: update it by hand (see below).

### With Docker

The installer asks where to keep the Compose file (by default `~/meshloom`), then writes there:

- `docker-compose.yml`, with port 8000, a `./data` folder for the database and, if found, the USB radio and the Bluetooth system connection (D-Bus);
- `.env`, which holds `MESHLOOM_IMAGE`, the image to run.

On a new installation with a USB radio, Meshloom runs in the container as user 10001 instead of root (`MESHLOOM_RUN_AS_USER`, see below). Remove that line if you switch to Bluetooth or if the radio stops answering.

An existing `docker-compose.yml` is backed up first. If you edited it, the installer offers to keep your edits.

## Running `install_service.sh` yourself

From a copy of the code:

```bash
bash scripts/setup/install_service.sh
```

It needs `uv` and Python 3.11 or newer. It asks whether to build the interface with Node.js 22.12 and npm 10 or newer, or to download a ready-made one. It then writes `/etc/systemd/system/meshloom.service`, starts it as your user, and gives the user access to serial and Bluetooth devices.

You can run it again after updating the code: it stops the service, rewrites the unit, reloads systemd and starts it again. It does not set up extensions or a password. To set variables, see [Variables and settings](/en/docs/deep/environment/).

To update such an installation, fetch the new code, then run the script again. It installs the dependencies, rebuilds or downloads the interface, and restarts the service. A copy made by the installer is pinned to a release: replace `X.Y.Z` with the number of the latest version, shown on the [release page](https://github.com/TwinRocket/meshloom/releases).

```bash
cd ~/meshloom
git fetch --depth 1 origin tag X.Y.Z
git checkout X.Y.Z
bash scripts/setup/install_service.sh
```

If you cloned the `main` branch yourself, `git pull` replaces the two `git` lines.

## Docker by hand

The image is `ghcr.io/twinrocket/meshloom`. The repository contains `docker-compose.example.yml`. Its main parts:

- `image: ${MESHLOOM_IMAGE:-ghcr.io/twinrocket/meshloom:latest}`. Without a `.env` file it follows `:latest`. To stay on a given release, write the line `MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:X.Y.Z@sha256:...` in a `.env` file next to it. Every release publishes its digests in a signed `OCI-DIGESTS` file.
- `ports: "8000:8000"`.
- `./data:/app/data`, for the database.
- `devices:`, to give the container a USB radio. The file contains an example path that does not exist on your machine: replace it with the path of your radio, or delete the two lines if your radio is on the network or on Bluetooth, otherwise the container does not start. The radio itself is chosen in the web interface.
- `MESHCORE_DATABASE_PATH: data/meshcore.db`, and `restart: unless-stopped`.

The container runs as root by default. To run it as user number 10001 instead, set `MESHLOOM_RUN_AS_USER: "10001"`. At startup, the container hands `./data` to that user and gives it the groups of the serial devices. If the radio still cannot be opened that way, Meshloom stays root and writes a warning in its log. As user 10001, Meshloom keeps only the two rights it needs to run plugins under their own account (`CAP_SETUID`, `CAP_SETGID`). This mode is the default for new Docker installations with a USB radio. It protects you against mistakes and against plugins, which never get those rights. It does not stop a real attack on Meshloom itself: someone who takes over the Meshloom process could use these rights to become root again inside the container, though not on the host. There is no setting that removes them while keeping plugins under their own account. Without the line, Meshloom runs fully as root, which is no safer. Before opening Meshloom to the Internet, read [Security](/en/docs/deep/security/). Do not use it with Bluetooth. Bluetooth needs more manual setup, described in [Radio transports](/en/docs/deep/transports/).

Plugins run in the container under user numbers 40000 to 49999 (see [Plugins](/en/docs/deep/plugins/)). Docker does not translate user numbers unless you turn on `userns-remap`, so on the host they are the same numbers. Meshloom skips a number that has an account inside the container, but it cannot see the host's accounts: if the host uses numbers in that range (often through a directory such as LDAP), a plugin's processes and the files it leaves in folders shared with the host belong to that account there. Leave the range unused on the host, or turn on `userns-remap`.

Several Meshloom containers on one host share these numbers too. Each Meshloom notes the numbers it uses in a `.meshloom-plugin-uids` folder of its temporary folders (`/tmp`, `/var/tmp`, `/dev/shm`) and never takes, nor cleans, a number another one noted there, so two copies that share `/tmp` or the IPC namespace (`--ipc=host`) stay apart. Copies that share nothing still start at different numbers. If you remove a Meshloom for good while its temporary folders stay (no reboot), its numbers stay taken until you delete its notes in `/tmp/.meshloom-plugin-uids`; Settings → Extensions says so when a number is blocked this way.

A Compose file written by hand cannot be updated from the interface. The installer's Docker mode adds a small helper that runs as root on the host: it pins the image by its signed digest in `.env` and installs updates when you ask in **Settings → Updates**. It never edits `docker-compose.yml`. Docker Desktop and rootless Docker get no helper: the stack follows `:latest`.

## Portainer

The repository's [`docker-compose.dev.yaml`](https://github.com/TwinRocket/meshloom/blob/main/docker-compose.dev.yaml) is meant for a stack that **follows the development version** (`main`), using the image published for each change (`ghcr.io/twinrocket/meshloom:edge`). In Portainer, point the stack at it and load the variables of `.env.example`:

```text
MESHLOOM_HTTP_PORT=8123
MESHLOOM_DATA_PATH=/opt/docker/meshloom/data
MESHCORE_DATABASE_PATH=data/meshcore.db
MESHCORE_DISABLE_PLUGINS=false
```

The file also publishes port 5001 (`MESHLOOM_PROXY_PORT`), used by the radio proxy once you enable it. To stay on released versions, use `docker-compose.example.yml` instead.

Nothing is built on your server. The image is published a few minutes after each change, but Portainer updates the stack as soon as it sees the change, so it usually fetches the previous image and keeps it until the next change. To get the latest one, redeploy the stack by hand a few minutes later: Meshloom fetches the newest image at each deployment. Portainer Business Edition can also do it on its own with **Force redeployment**.

Older versions of this file built the image on the server. Portainer copies each new version over the old files without deleting the ones removed from the repository, and such a leftover could make the build fail (`npm run build` ending with `exit code: 2`). A stack stuck on that error gets the new file at its next update. If it still fails, delete the stack and create it again with the same variables and data folder: your database is not touched.

The push notification contact is set in **Settings → Notifications**, and the radio's address and port in the web interface. A new, empty data folder starts with Community off: join it in the settings. See [Meshloom Community](/en/docs/deep/community/).

## From a checkout, for development

```bash
uv sync
uv run uvicorn app.main:app --reload --no-proxy-headers
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
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers
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

The recovery command `meshloom-admin` reads the same file; how to run it for each installation is in [Accounts and access](/en/docs/deep/access/).

**Settings → Updates** shows the update status and can check for a new version (**Check now**). Where it is supported, it installs the update from a signed source, either on request (**Install now**) or by itself inside a time window and on the days you choose. This works for a Linux package and for a Docker stack managed by the helper. A copy of the code is updated as described above. Elsewhere, update by hand:

```bash
sudo apt-get install --only-upgrade meshloom
sudo dnf upgrade meshloom
sudo docker compose pull && sudo docker compose up -d
```

The first line is for Debian, Ubuntu and Raspberry Pi OS, the second for Fedora and similar systems, the third for Docker.

Started by hand without `--host`, Meshloom answers only on the machine itself, at `http://127.0.0.1:8000`. The page at `/docs` documents the programming interface (API). It is not this website.
