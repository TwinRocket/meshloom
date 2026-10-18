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
| Pi 2, or a Pi 3 or later already running 32-bit Raspberry Pi OS | The [one-liner](/en/docs/install/). It detects the architecture and installs the 32-bit (armhf) package. |
| Pi 1, Compute Module 1, original Zero and Zero W | Not supported. These boards use an older processor (ARMv6) and no package is built for it. The one-liner says so and stops before changing anything. |

Prefer the 64-bit image on any board that can run it: it is faster, and the 32-bit package is built under emulation. A Pi 2 can only run 32-bit, so the one-liner is its route.

The 32-bit package has existed since 4.12.2. Earlier versions could not be installed on a 32-bit system.

### What to expect on a Pi 2 or Pi 3

Nobody has published measurements for these boards, so take what follows as what is known, not as a promise.

Both have 1 GB of memory, and most of what looks heavy in Meshloom is not heavy on the Pi: the map, the 3-D view and the packet feed are drawn by the browser you open the interface in. The Pi only runs the server, the link to the radio and an SQLite database.

The likelier limit is the SD card. The database is written to for every message and every packet heard. A cheap card is slow at that and wears out. If you plan to leave a node running for months, a good card or a USB SSD matters more than the board.

A Pi 2 is the slowest supported board, and the interface will feel it when the history grows. If you try one, tell us where it stops being comfortable: nobody has that number yet.

## Write the card

You need a computer with an SD card reader and [Raspberry Pi Imager](https://www.raspberrypi.com/software/), **version 2.0.6 or newer**. Older 1.9.x versions do not apply the first-boot settings (cloud-init) used by this OS, so the host name, Wi-Fi and SSH choices would be ignored.

1. Go to the [GitHub releases page](https://github.com/TwinRocket/meshloom/releases) and download `meshloom.rpi-imager-manifest` from the latest release. It is attached a little after the packages, so wait if you do not see it yet. You do not need to download the image (`meshloom-rpi-lite-arm64.img.xz`): the manifest points to it, and Imager downloads it from the same release.
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

## Kiosk image (screen on the wall)

The kiosk image shows Meshloom full screen on a display plugged into the Pi, with a read-only `kiosk` account that has no password: it reads channel messages, never direct messages, and has no settings.

On a new kiosk image, or on a kiosk image flashed before 5.0 once it has been updated, the screen first shows **Administrator account not created yet** with an installation code. From a phone or a computer on the same network, open `http://meshloom.local:8000`, type that code and create the administrator account. A few seconds later the wall screen opens Meshloom by itself, signed in as the kiosk account. The code only appears on the Pi’s own screen; to get a new one, run `meshloom-admin setup-code` on the Pi.

The screen signs in with a **device token** that Meshloom writes on the Pi, in `/var/lib/meshloom-kiosk/kiosk.token`: only the desktop user of the Pi can read it, the token never appears in an address or in the list of running programs, and it only works from the Pi itself. No trusted network is needed. In **Settings → Users & Access → API and device tokens**, an administrator sees it as **Kiosk screen** (owner `kiosk`): **Revoke** replaces it with a new one at once: the screen is signed out, then signs in again with the new token within a few seconds. To keep the screen signed out, disable the `kiosk` account in **Users & Access**. The token only opens the screen's session on the Pi itself: a copy used from another machine, or on any other address of the API, is refused.

On a kiosk image flashed before 5.0, restart the Pi once after the update: the desktop session then gets access to the token and the screen signs in.

To make the wall screen open on the Feed (or any tool) after a reboot, an administrator sets **Settings → Navigation → Start page**: it applies to every session, the kiosk account included, and needs no SSH. `MESHLOOM_KIOSK_URL` remains a bootstrap variable of the launcher on a hand-built image; a start page only applies when that address has no `#` part.

## Update

When the image (or any Linux package install) can update itself, **Settings > Updates** offers **Install now** and an optional automatic update. This only updates Meshloom, from the signed Meshloom repository, never the whole operating system. Images from 4.18 on are ready for this out of the box. On an older image, one `sudo apt update && sudo apt install meshloom` enables it.

If **Settings > Updates** says you must update Meshloom by hand, follow the steps it shows, or run the installer again so it can restore the missing helper:

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

Home Assistant add-ons update inside Home Assistant, not with this button.

Meshloom Community is off on a new database, like on every other install. You join it by entering an airport code in **Settings > Meshloom Community**; nothing is published before that.
