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
   - **the radio did not give its private key**: the radio was probably not ready. Check that the status bar shows **Radio OK**, then download the backup again. If the note comes back, press **Disconnect** then **Reconnect** in **Settings → Radio**, wait for **Radio OK**, and download it once more. Do not reflash with a backup that has no identity.
5. Keep the file somewhere safe, away from the radio's machine if possible. It contains secrets: see [Encryption](#encryption).

An **update without wipe** keeps the radio's identity and settings: it needs no restore. A firmware built without Wi-Fi credentials then relies on the ones the radio stores, so make sure the radio stores them before such an update.

## Where the backup is

The backup lives in **Settings → Database**, group **Backup & Restore**. You can open it directly with the address of your Meshloom followed by `/#settings/database`, for example `http://meshloom.local:8000/#settings/database`. Recent versions also accept `/#settings/database/backup`, which scrolls to the group.

The group holds:

- **Full backup (radio and Meshloom)**: the file described on this page.
- **Restore a backup**: restores a full backup, or an older Meshloom-only backup.
- **Download meshcore.db**: a copy of the whole database (message history included). It never contains the radio's private key, the Bluetooth PIN or the keys described under "Not included" below, and is not restored from the interface. If you put such a copy back in place of the database yourself, Meshloom treats it as a restore when it next starts (see [What the backup contains](#what-the-backup-contains), "Not included").

## Restore after reflashing

1. Flash the radio, then connect it to Meshloom as usual (**Settings → Radio**).
2. Meshloom sees a radio with a new identity and opens the identity window. Choose **Restore from a backup**, pick the file (and type its passphrase if you set one).
3. Meshloom shows what the file holds and what restoring it changes. The identity of the connected radio, a new random one after a full install, is replaced by the one in the backup: the window names that radio and asks you to confirm.
4. Click **Restore**. Meshloom writes the identity, then its own data, then the radio settings, channels, contacts and Wi-Fi credentials, and shows each step as it goes. When it is done, it lists what did not go as planned: a radio setting the radio refused, contacts that did not fit, bots that were skipped, push keys that were kept.
5. When it asks, click **Reboot the radio now** so the radio applies the restored settings (Wi-Fi in particular). It asks only when the restored Wi-Fi settings differ from what the radio runs on. Meshloom expects this reboot: it waits for the radio without reporting connection errors, and when the radio comes back on Wi-Fi, it offers to use it over TCP (see [Transports](/en/docs/deep/transports/#from-bluetooth-to-wi-fi)). **Settings → Radio** then shows the restored Wi-Fi credentials.

On a **new Meshloom** (a new machine or a new database), **set up the radio connection first**, then restore from **Settings → Database → Restore a backup**. If the radio is not connected when you restore, only the Meshloom data is restored. When the radio connects later, Meshloom asks what to do with its identity: choose **Restore from a backup** again so the key and the radio steps are applied, or **Bind without wipe** to keep the radio's own identity with the restored history. When this Meshloom already belongs to the backup's identity, it asks nothing when the radio connects: restore again once the radio is connected to apply the radio steps. It asks nothing either when the backup holds no contacts and no groups (there is then no history to protect, and Meshloom simply adopts the radio that connects): restore again once the radio is connected. Meshloom never ties itself to the backup's identity before a radio has shown it holds it.

When the history in Meshloom was recorded before it knew which identity it belongs to (an older version, or a database never tied to a radio), the summary offers **Also erase the history this Meshloom recorded before**, unticked: left unticked, that history is kept and attributed to the restored identity.

Some steps can be skipped:

- A radio whose firmware refuses key import (built without it) cannot get its identity back. Meshloom stops before writing anything else and says so.
- If the radio restarts during the restore (it can, to set its clock), the remaining steps are skipped: run the restore again once the radio is back.
- The Bluetooth PIN step is off by default when Meshloom reaches the radio over Bluetooth and the PIN differs; the Wi-Fi step is off by default when Meshloom reaches the radio over Wi-Fi and the backup holds another network or turns Wi-Fi off, since restoring it would cut Meshloom off.

After a restore, join **Meshloom Community** again if you want it: participation is never restored from a file. Push notifications may need to be turned on again in each browser.

## Encryption

The full backup contains your radio's private key and, if the radio stores them, its Wi-Fi password, plus other secrets kept by Meshloom: channel keys, the Bluetooth PIN, the SMTP password, webhook and integration secrets. Anyone holding the file can impersonate your node. With **Include accounts** checked (the default), it also contains the password hashes and two-factor secrets of every account: anyone holding it can try to guess the passwords offline. Uncheck the box before sending a backup to someone else, for instance for help.

Tick **Encrypt with a passphrase** before downloading to protect it (8 characters or more). The file is then unreadable without the passphrase, and nobody can recover a lost passphrase. Meshloom asks for it when you restore. Without encryption, treat the file like the key itself. An encrypted backup is limited to 16 MiB; Meshloom refuses to encrypt a larger one (thousands of contacts, large bots), which then downloads only without encryption.

Note that anyone who can reach your Meshloom can download this backup: keep Meshloom on a network you trust (see [Security](/en/docs/deep/security/#the-nodes-private-key)).

## What the backup contains

**From the radio:**

- Its identity (private key), when the firmware allows exporting it.
- Its name, position, transmit power and radio parameters (frequency, bandwidth, spreading factor, coding rate), with the client repeat flag.
- Its other settings: telemetry sharing, advert location policy, extra acknowledgements, manual contact adding, timing parameters, path hash mode, auto-add rules, the default flood scope (region), the Bluetooth PIN, firmware custom variables (GPS and its interval when the board has them) and, on firmware 1.18 and later, the time zone and the radio tuning settings only the command line reaches.
- Whether the radio's Wi-Fi is on or off, and the Wi-Fi network name and password when the radio stores a password. An open network is saved when it was stored from Meshloom (with **Open network**, or by an earlier restore), since the radio cannot tell it from "nothing stored"; a restore stores it again. Credentials only built into the firmware are not saved (the on/off setting is). When the radio stores a password but no network name, the radio reports the network name built into the firmware: the backup pairs those two, and a restore stores both (the radio then uses them).

**From Meshloom:**

- Contacts (favourites, routes), channels with their keys and region scope, contact groups.
- The settings: regions, block lists, telemetry tracking, alert rules, notification destinations, updates, interface preferences, push preferences, the radio proxy settings, the **Workarounds** group of **Settings → Radio → Advanced** (the clock fix included).
- MQTT, webhook, Apprise, SQS, map upload and Home Assistant integrations, with their secrets, each enabled or disabled as in the file: an enabled one starts sending right after the restore, except one allowed to reach a private network, which comes back disabled with a notice: check its address, then enable it. **Bots come back disabled**: a bot runs code on the server, so enable each one after reading it. On a server where bots are turned off (`MESHCORE_DISABLE_BOTS=true`, the default of the Linux package and the Raspberry Pi image, or **Disable Bots Until Server Restart**), bots are **not restored at all**: turn bots on first if you want them back. Before you restore, the summary names each integration, says whether the file enables the radio proxy, and says which bots are skipped.
- The Web Push keys and subscriptions, restored only on a Meshloom that has no subscriptions of its own. Restored subscriptions receive your message notifications at once, so the summary names the address each one sends to before you restore.
- The accounts, when **Include accounts** is checked (it is by default): user names, display names, roles, Home Assistant identities, disabled accounts, password hashes and two-factor secrets. Login sessions, the audit log, pairing codes and the Meshloom Remote identity are never in the file. Restoring a file with accounts replaces the accounts of this Meshloom and signs every session out: sign in again with an account from the file. A file whose accounts include no enabled administrator who can sign in on this Meshloom (an account with a password, or a Home Assistant user when restoring on the add-on) is not used for accounts (the current ones stay), so a restore never locks you out: an add-on backup whose only administrator is a Home Assistant user keeps the current accounts on Docker or the Linux package. A file without accounts keeps the current accounts. A trusted network whose account is not the same person after the restore is removed. The API and device tokens come with the accounts (their fingerprints only, never the tokens themselves), so the devices keep working; a token whose account is missing from the file, or whose account's role no longer allows its scopes, is not restored, and a file with accounts but no tokens leaves none.

Newer versions of Meshloom add more parts to this file. A part the running version does not know is left aside, and the rest is restored; a part missing from the file leaves the matching data as it is. If one part cannot be restored, it is left as it was, the others still are, and the restore says which part failed. A part made by a newer Meshloom than yours is not restored: update Meshloom first. Before you restore, the summary lists these parts and marks the ones that contain secrets (such as accounts); when a backup holds such parts, encrypt it (see [Encryption](#encryption)).

**Restore a full backup on Meshloom 5.0 or later only.** Meshloom 4.18 and earlier read it as an older Meshloom-only backup: they restore the contacts, channels, settings and groups, and silently drop the radio's identity and settings, the Wi-Fi credentials, the integrations, the push keys and every added part, without saying so. A restore file is limited to 64 MiB.

**Not included:**

- Messages, raw packets and telemetry history: use **Download meshcore.db** for those.
- The radio connection settings (port, address, Bluetooth device): they belong to the machine.
- Meshloom Community participation and its region code: joining is your choice, made again.
- The link and file sharing consent: a backup restored on another machine would otherwise switch on a service nobody agreed to there, so sharing stays off (or as it is) after a restore. How links are shortened and when previews load are restored.
- The radio's clock (Meshloom sets it) and a few settings no command reaches (buzzer and vibration).
- The keys Meshloom uses to identify itself to online services (remote access, link sharing). They never leave the server, not even in a full backup that holds the radio's private key, nor in a `meshcore.db` download: Meshloom creates new ones after every restore, including a `meshcore.db` copy put back in place (made by this version or a later one), so those services need linking again.

Channels are written into the radio's slots by the restore, and the slots above them are emptied. Meshloom manages those slots itself on every connection: they are filled again as you use the channels.
