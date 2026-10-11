---
title: Raspberry Pi image
description: Write a card with Meshloom already installed, set Wi-Fi in Raspberry Pi Imager, and boot.
level: start
order: 2.5
---

The published image is Raspberry Pi OS Lite, 64-bit, with Meshloom already installed. The first boot does not need Internet access.

## Which Pi?

| Your board | How to install |
|---|---|
| Pi 3B / 3B+ / 3A+, Compute Module 3, Zero 2 W, Pi 4, Pi 5 | This image. It is the quickest way, and nothing else is needed. |
| Pi 2, or any Pi already running 32-bit Raspberry Pi OS | The [one-liner](/en/docs/install/). It detects the architecture and installs the 32-bit (armhf) package. |
| Pi 1, Compute Module 1, original Zero and Zero W | Not supported. These boards use an older processor (ARMv6) and no package is built for it. |

Prefer the 64-bit image on any board that can run it: it is faster, and the 32-bit package is built under emulation. A Pi 2 can only run 32-bit, so the one-liner is its route.

The 32-bit package has existed since 4.12.2. Earlier versions could not be installed on a 32-bit system.

### What to expect on a Pi 2 or Pi 3

Nobody has published measurements for these boards, so take what follows as what is known, not as a promise.

Both have 1 GB of memory, and most of what looks heavy in Meshloom is not heavy on the Pi: the map, the 3-D view and the packet feed are drawn by the browser you open the interface in. The Pi only runs the server, the link to the radio and an SQLite database.

The likelier limit is the SD card. The database is written to for every message and every packet heard. A cheap card is slow at that and wears out. If you plan to leave a node running for months, a good card or a USB SSD matters more than the board.

A Pi 2 is the slowest supported board, and the interface will feel it when the history grows. If you try one, tell us where it stops being comfortable: nobody has that number yet.

## Write the card

You need a computer with an SD card reader and [Raspberry Pi Imager](https://www.raspberrypi.com/software/), **version 2.0.6 or newer**. Older 1.9.x versions do not apply the first-boot settings (cloud-init) used by this OS, so the host name, Wi-Fi and SSH choices would be ignored.

1. Go to the [GitHub releases page](https://github.com/TwinRocket/meshloom/releases) and download two files from the latest release: `meshloom-rpi-lite-arm64.img.xz` **and** `meshloom.rpi-imager-manifest`. They are attached a little after the packages, so wait if you do not see them yet.
2. Open the manifest instead of the image: double-click `meshloom.rpi-imager-manifest`, or run `rpi-imager --repo path/to/meshloom.rpi-imager-manifest`. This file tells Imager how to apply your settings (`init_format: cloudinit-rpi`). Do **not** use *Use custom* on the `.img.xz` alone: Imager 2.x then assumes there is nothing to customise and skips Wi-Fi, user and SSH.
3. In Imager, choose your Pi model, then Meshloom, then your card.
4. Fill in the host name, the user, your SSH key if you want one, and the Wi-Fi. These settings are written to the card by Imager. They are not inside the download.
5. Write the card, put it in the Pi and power it on.

Ethernet and Wi-Fi are both optional at first boot: the Pi starts even if no cable is plugged in or the Wi-Fi network is unavailable.

## Open Meshloom

If a screen is connected to the Pi, it shows a "Meshloom is ready" message with the addresses to use:

```
http://meshloom.local:8000
http://<ip-address>:8000
```

Without a screen, use the `.local` name from another device on the same network, or the address your router gave the Pi. The `.local` name is the host name you chose in Imager, followed by `.local` (`meshloom` if you left the default). Then plug in the radio and choose it in **Settings > Radio** (see [First launch](/en/docs/first-run/)).

With no network at all, open `http://127.0.0.1:8000` on the Pi itself. Messaging and the radio work offline. Maps, Meshloom Community, Web Push and in-app updates wait until the Pi can reach the Internet.

Bots, which run code on the machine, are turned off on this image, like in every Linux package. See [A trusted network](/en/docs/trust/).

## Update

When the image (or any Linux package install) can update itself, **Settings > Updates** offers **Install now** and an optional automatic update. This only updates Meshloom, from the signed Meshloom repository, never the whole operating system. Images from 4.18 on are ready for this out of the box. On an older image, one `sudo apt update && sudo apt install meshloom` enables it.

If **Settings > Updates** says you must update Meshloom by hand, follow the steps it shows, or run the installer again so it can restore the missing helper:

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

Home Assistant add-ons update inside Home Assistant, not with this button.

A new database joins Meshloom Community by default, like on every other install. Nothing is published until you enter an airport code in **Settings > Meshloom Community**.
