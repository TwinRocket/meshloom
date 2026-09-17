---
title: Raspberry Pi image
description: Flash a Lite 64-bit card with Meshloom already installed, then set Wi-Fi in Imager.
level: start
order: 2.5
---

A published image is Raspberry Pi OS Lite 64-bit with Meshloom already installed. First boot does not need the internet. Use it on a Pi 3B / 3B+ / 3A+, Compute Module 3, Zero 2 W, Pi 4, or Pi 5. Flash the **64-bit** image even on a Pi 3 — the 32-bit OS cannot install the Meshloom package.

## Flash

1. Download `meshloom-rpi-lite-arm64.img.xz` from the [GitHub release](https://github.com/TwinRocket/meshloom/releases). The Release workflow attaches it after the arm64 `.deb` is built (it can land a bit later than the packages).
2. Open **Raspberry Pi Imager 2.0.6 or newer**. Older 1.9.x builds do not write Trixie cloud-init customisation, so hostname, Wi-Fi, and SSH are ignored.
3. Choose the Meshloom image (or *Use custom*). If Imager hides the customisation panel, load the `cloudinit-rpi` manifest shipped next to the image.
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
