---
title: Raspberry Pi image
description: Flash a Lite 64-bit card with Meshloom already installed, then set Wi-Fi in Imager.
level: start
order: 2.5
---

A published image is Raspberry Pi OS Lite 64-bit with Meshloom already installed. First boot does not need the internet.

## Which Pi

| Your board | How to install |
|---|---|
| Pi 3B / 3B+ / 3A+, Compute Module 3, Zero 2 W, Pi 4, Pi 5 | This image. It is the fastest route and nothing else is needed. |
| Pi 2, or any Pi already running the 32-bit Raspberry Pi OS | The [one-liner](/en/docs/install/). It detects the architecture and installs the armhf package. |
| Pi 1, Compute Module 1, original Zero and Zero W | Not supported. These are ARMv6 and no package is built for them. |

Prefer the 64-bit image on a board that can run it: it is faster, and the 32-bit package is built under emulation. A Pi 2 only ever runs 32-bit, so the one-liner is its route.

The 32-bit package is recent, published in 4.12.2. Earlier versions could not install on a 32-bit system at all.

### What to expect on a Pi 2 or Pi 3

Nobody has published measurements for these boards, so treat what follows as what
is known rather than as a promise.

Both have 1 GB of memory, and most of what looks expensive in Meshloom is not on
the Pi: the map, the 3-D view and the packet feed are drawn by whatever browser
you open the interface in. What the Pi runs is the server, the radio link and an
SQLite database.

The likelier limit is the SD card. The database is written to on every message and
every observed packet, and a cheap card is slow at exactly that and wears out
doing it. If you plan to leave a node running for months, a good card, or a USB
SSD, matters more than the board.

A Pi 2 is the slowest of the supported boards and the interface will feel it when
the history grows. If you try one, tell us where it stops being comfortable: that
is a number nobody has yet.

## Flash

1. Download `meshloom-rpi-lite-arm64.img.xz` **and** `meshloom.rpi-imager-manifest` from the [GitHub release](https://github.com/TwinRocket/meshloom/releases). The Release workflow attaches them after the arm64 `.deb` is built (they can land a bit later than the packages).
2. Open **Raspberry Pi Imager 2.0.6 or newer**. Older 1.9.x builds do not write Trixie cloud-init customisation, so hostname, Wi-Fi, and SSH are ignored.
3. Open the manifest (double-click it, or `rpi-imager --repo path/to/meshloom.rpi-imager-manifest`). That file sets `init_format: cloudinit-rpi`. Do **not** pick *Use custom* on the `.img.xz` alone — Imager 2.x then assumes no customisation and skips Wi-Fi, user, and SSH.
4. Set hostname, user, SSH key, and Wi-Fi there. Do not expect those secrets to be inside the download.
5. Write the card, boot the Pi.

On first boot, Ethernet and Wi-Fi are optional: the machine starts even if no cable or SSID is ready.

## Open Meshloom

If a screen is plugged in, the console shows:

```
http://meshloom.local:8000
http://<lan-ip>:8000
```

Without a screen, use the `.local` name from another device on the same network, or the address your router gave the Pi. Then choose the radio under **Settings → Radio**.

Without any network, open `http://127.0.0.1:8000` on the Pi itself. Messaging and the radio work offline. Maps, Community, Web Push, and in-app updates wait until the machine can reach the internet.

## Update

When the image (or a Linux package install) can apply updates, **Settings → About** offers *Install* and an optional automatic update. That path upgrades Meshloom only, not the whole operating system.

If About says you must update Meshloom manually, use the recipe it shows, or re-run the Linux installer so it can install the missing helper:

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

Home Assistant add-ons update in Home Assistant, not from this button.

`MESHLOOM_COMMUNITY` is not set on the image. A new database joins Community by default, same as every other install.
