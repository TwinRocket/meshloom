# Meshloom

[![All Quality](https://github.com/TwinRocket/meshloom/actions/workflows/all-quality.yml/badge.svg)](https://github.com/TwinRocket/meshloom/actions/workflows/all-quality.yml)
[![Release](https://img.shields.io/github/v/release/TwinRocket/meshloom?logo=github)](https://github.com/TwinRocket/meshloom/releases)
[![License](https://img.shields.io/github/license/TwinRocket/meshloom)](LICENSE.md)

Web interface for a MeshCore companion radio: messages, contacts, and mesh observation in the browser. The server talks to the radio over USB, TCP, or Bluetooth and keeps history beyond the device’s memory.

Fork of [Jack Kingsman’s MeshCore web client](https://github.com/jkingsman/Remote-Terminal-for-MeshCore), via [Ian Langworth](https://github.com/statico/remoteterm-meshcore). Original copyright remains in [LICENSE.md](LICENSE.md).

**Trusted network only.** There are no user accounts, and bots can run arbitrary Python. Optional HTTP Basic auth (`MESHCORE_BASIC_AUTH_USERNAME` / `MESHCORE_BASIC_AUTH_PASSWORD`) is a coarse gate and needs HTTPS. Set `MESHCORE_DISABLE_BOTS=true` to turn bots off.

Meshloom takes over radio contacts and channels. A poor fit if you swap radios and expect the device to keep its own favorites independently of the app.

![Screenshot of the web interface](app_screenshot.png)

## Install

On Linux the installer offers a native systemd service or Docker. Radio transport is configured in the web UI after install. Use `bash -c` so prompts still have a terminal — do not pipe into `bash`.

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

Then open http://127.0.0.1:8000 and choose the radio under **Settings > Radio**. A new install joins Meshloom Community unless you set `MESHLOOM_COMMUNITY=0`; leave or bind an IATA code under **Settings > Community**. User-facing docs live in [`docs/user/`](docs/user/) and are published at https://meshloom.app/docs/.

A **Raspberry Pi** can skip the one-liner: flash `meshloom-rpi-lite-arm64.img.xz` from the release (Lite 64-bit, Pi 3 and later). Set Wi-Fi, user, and SSH in **Raspberry Pi Imager 2.0.6+**. A connected screen shows `http://meshloom.local:8000`. Details: [Raspberry Pi image](docs/user/en/rpi.md).

From a checkout (development): [CONTRIBUTING.md](CONTRIBUTING.md). Docker image: `ghcr.io/twinrocket/meshloom`. Portainer, HTTPS, systemd, and extra environment variables: [README_ADVANCED.md](README_ADVANCED.md).

### On Home Assistant

Meshloom runs as an add-on. The button opens the dialog on your own instance with
the repository filled in — it is a redirector and learns nothing about you:

[![Add the repository to your Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FTwinRocket%2Fmeshloom)

Or add `https://github.com/TwinRocket/meshloom` by hand under **Settings → Add-ons →
Add-on store → ⋮ → Repositories**, then install **Meshloom**.

The web interface arrives in the sidebar; the radio proxy is the only thing
published on the host, and its port is set in the add-on's **Network** panel.
Details: [`meshloom/DOCS.md`](meshloom/DOCS.md).

This is not the same as [publishing to Home Assistant over MQTT](README_HA.md),
which works from any install and needs no add-on.

## Update

On a package or installer-managed Docker install, **Settings → About** can install the new Meshloom release (optional automatic updates). That upgrades Meshloom only.

Otherwise update by hand, or re-run the installer so it can install the apply helper if it is missing:

```bash
sudo apt-get install --only-upgrade meshloom   # Debian / Ubuntu (Meshloom only)
sudo dnf install meshloom                      # Fedora / Rocky / Alma
sudo docker compose pull && sudo docker compose up -d
```

Home Assistant: update the add-on there. The database stays in place (`/var/lib/meshloom` for the package, `./data` for Docker). Schema migrations run on startup.

## More

- User docs (source): [`docs/user/`](docs/user/) — published at https://meshloom.app/docs/
- API docs once the server is up: http://127.0.0.1:8000/docs
- Home Assistant — publishing the mesh over MQTT: [README_HA.md](README_HA.md)
- Home Assistant — running Meshloom as an add-on: [`meshloom/DOCS.md`](meshloom/DOCS.md)
