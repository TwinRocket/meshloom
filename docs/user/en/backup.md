---
title: Back up your radio
description: Save the radio's identity, settings, contacts and channels before reflashing, and restore them after.
level: start
order: 5.5
---

Meshloom can save everything that makes your node yours into one file, the **full backup**: the radio's identity (its private key), its settings, the Wi-Fi credentials it stores, your contacts and channels, and the Meshloom settings. Restoring it on a freshly flashed radio brings the same node back: same public key, same contacts, same channels.

## Before reflashing

Do this before a **full install** with the flasher, or before any flash that erases the radio:

1. Keep the radio connected to Meshloom, with the status bar showing **Radio OK**.
2. Open Meshloom, then **Settings → Database** (see [Where the backup is](#where-the-backup-is)).
3. In **Backup & Restore**, under **Full backup (radio and Meshloom)**, click **Download the full backup**.
4. Read the notes Meshloom shows after the download. If it says **this backup has no identity**, read why:
   - **the radio firmware does not allow exporting its private key**: no backup can carry this node's identity. The file holds the settings, contacts and channels, but restoring it will not bring the identity back.
   - **the radio did not give its private key**: the radio was probably not ready. Check that the status bar shows **Radio OK**, then download the backup again.
5. Keep the file somewhere safe, away from the radio's machine if possible. It contains secrets: see [Encryption](#encryption).

An **update without wipe** keeps the radio's identity and settings: it needs no restore. A firmware built without Wi-Fi credentials then relies on the ones the radio stores, so make sure the radio stores them before such an update.

## Where the backup is

The backup lives in **Settings → Database**, group **Backup & Restore**. You can open it directly with the address of your Meshloom followed by `/#settings/database`, for example `http://meshloom.local:8000/#settings/database`. Recent versions also accept `/#settings/database/backup`, which scrolls to the group.

The group holds:

- **Full backup (radio and Meshloom)**: the file described on this page.
- **Restore a backup**: restores a full backup, or an older Meshloom-only backup.
- **Download meshcore.db**: a copy of the whole database (message history included). It never contains the radio's private key and is not restored from the interface.

## Restore after reflashing

1. Flash the radio, then connect it to Meshloom as usual (**Settings → Radio**).
2. Meshloom sees a radio with a new identity and opens the identity window. Choose **Restore from a backup**, pick the file (and type its passphrase if you set one).
3. Meshloom shows what the file holds and what restoring it changes. The identity of the connected radio, a new random one after a full install, is replaced by the one in the backup: the window names that radio and asks you to confirm.
4. Click **Restore**. Meshloom writes the identity, then its own data, then the radio settings, channels, contacts and Wi-Fi credentials, and shows each step as it goes. When it is done, it lists what did not go as planned: a radio setting the radio refused, contacts that did not fit, bots that were skipped, push keys that were kept.
5. When it asks, click **Reboot the radio now** so the radio applies the restored settings (Wi-Fi in particular).

On a **new Meshloom** (a new machine or a new database), **set up the radio connection first**, then restore from **Settings → Database → Restore a backup**. If the radio is not connected when you restore, only the Meshloom data is restored. When the radio connects later, Meshloom asks what to do with its identity: choose **Restore from a backup** again so the key and the radio steps are applied, or **Bind without wipe** to keep the radio's own identity with the restored history. When this Meshloom already belongs to the backup's identity, it asks nothing when the radio connects: restore again once the radio is connected to apply the radio steps. Meshloom never ties itself to the backup's identity before a radio has shown it holds it.

When the history in Meshloom was recorded before it knew which identity it belongs to (an older version, or a database never tied to a radio), the summary offers **Also erase the history this Meshloom recorded before**, unticked: left unticked, that history is kept and attributed to the restored identity.

Some steps can be skipped:

- A radio whose firmware refuses key import (built without it) cannot get its identity back. Meshloom stops before writing anything else and says so.
- If the radio restarts during the restore (it can, to set its clock), the remaining steps are skipped: run the restore again once the radio is back.
- The Bluetooth PIN step is off by default when Meshloom reaches the radio over Bluetooth and the PIN differs; the Wi-Fi step is off by default when Meshloom reaches the radio over Wi-Fi and the backup holds another network or turns Wi-Fi off, since restoring it would cut Meshloom off.

After a restore, join **Meshloom Community** again if you want it: participation is never restored from a file. Push notifications may need to be turned on again in each browser.

## Encryption

The full backup contains your radio's private key and, if the radio stores them, its Wi-Fi password, plus other secrets kept by Meshloom: channel keys, the Bluetooth PIN, the SMTP password, webhook and integration secrets. Anyone holding the file can impersonate your node.

Tick **Encrypt with a passphrase** before downloading to protect it (8 characters or more). The file is then unreadable without the passphrase, and nobody can recover a lost passphrase. Meshloom asks for it when you restore. Without encryption, treat the file like the key itself. An encrypted backup is limited to 16 MiB; Meshloom refuses to encrypt a larger one (thousands of contacts, large bots), which then downloads only without encryption.

Note that anyone who can reach your Meshloom can download this backup, and so can a web page opened in a browser on a machine that reaches it: keep Meshloom on a network you trust (see [Security](/en/docs/deep/security/#the-nodes-private-key)).

## What the backup contains

**From the radio:**

- Its identity (private key), when the firmware allows exporting it.
- Its name, position, transmit power and radio parameters (frequency, bandwidth, spreading factor, coding rate), with the client repeat flag.
- Its other settings: telemetry sharing, advert location policy, extra acknowledgements, manual contact adding, timing parameters, path hash mode, auto-add rules, the default flood scope (region), the Bluetooth PIN, firmware custom variables (GPS and its interval when the board has them) and, on firmware 1.18 and later, the time zone and the radio tuning settings only the command line reaches.
- Whether the radio's Wi-Fi is on or off, and the Wi-Fi network name and password when the radio stores a password. An open network, or credentials only built into the firmware, are not saved (the on/off setting is). When the radio stores a password but no network name, the radio reports the network name built into the firmware: the backup pairs those two, and a restore stores both (the radio then uses them).

**From Meshloom:**

- Contacts (favourites, routes), channels with their keys and region scope, contact groups.
- The settings: regions, block lists, telemetry tracking, alert rules, notification destinations, updates, interface preferences, push preferences, the radio proxy settings, the **Workarounds** group of **Settings → Radio → Advanced** (the clock fix included).
- MQTT, webhook, Apprise, SQS, map upload and Home Assistant integrations, with their secrets, each enabled or disabled as in the file: an enabled one starts sending right after the restore. **Bots come back disabled**: a bot runs code on the server, so enable each one after reading it. On a server where bots are turned off (`MESHCORE_DISABLE_BOTS=true`, the default of the Linux package and the Raspberry Pi image, or **Disable Bots Until Server Restart**), bots are **not restored at all**: turn bots on first if you want them back. Before you restore, the summary names each integration, says whether the file enables the radio proxy, and says which bots are skipped.
- The Web Push keys and subscriptions, restored only on a Meshloom that has no subscriptions of its own.

**Not included:**

- Messages, raw packets and telemetry history: use **Download meshcore.db** for those.
- The radio connection settings (port, address, Bluetooth device): they belong to the machine.
- Meshloom Community participation and its region code: joining is your choice, made again.
- The radio's clock (Meshloom sets it) and a few settings no command reaches (buzzer and vibration).

Channels are written into the radio's slots by the restore, and the slots above them are emptied. Meshloom manages those slots itself on every connection: they are filled again as you use the channels.
