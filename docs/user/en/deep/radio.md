---
title: Radio, contacts, and channels
description: What Meshloom loads onto the radio, flood scope, and hop width.
level: deep
order: 15
---

Meshloom manages the contacts and channels stored on your radio. The server remembers far more than the radio can hold, and loads onto the radio the working set it needs.

The database is tied to the public key of your radio. If you plug in a different radio, a window opens: **This radio does not match the stored identity**. **Wipe and continue** adopts the new radio and erases the local mesh contacts and messages; channels and server settings stay. **Cancel** keeps your data untouched and pauses the connection to that radio. A database created before this link existed asks once, even for its usual radio: **This radio is not bound to this instance**. Choose **Bind without wipe** if it is the same radio, or **New radio** to start clean. Both windows also offer **Restore from a backup**, which gives a reflashed radio its former identity back from a full backup (see [Back up your radio](/en/docs/backup/)). See [First launch](/en/docs/first-run/).

## Why contacts are loaded onto the radio

A radio can acknowledge incoming direct messages by itself, but only when the sender is in its contact table. So Meshloom reads that table, loads your favorites first, then fills it up to about 80% of **Max Contacts on Radio**. When the table reaches about 95% full, Meshloom clears it and reloads it in full.

Max Contacts on Radio is in **Settings → Radio → Messaging**. Lowering it makes Meshloom load fewer contacts.

## When the contact table is full

The table can fill up because of adverts, or because another app uses the same radio. With Bluetooth, reading a large table can also time out. In both cases Meshloom warns you that automatic acknowledgments may not work for every contact. You can:

- empty the table with another MeshCore app, then restart Meshloom;
- lower the number in **Max Contacts on Radio**;
- turn on auto-evict (below);
- ignore the warning. **Sending and receiving messages is never affected.**

### Auto-evict

**Let the radio evict its oldest contacts**, in **Settings → Radio → Advanced**, makes the radio drop its oldest non-favorite contact by itself when the table is full. It applies at the next connection. Turning it off later does not clear that preference on the radio: clear it with another MeshCore app. Adding contacts then never fails, Meshloom can load contacts even if it could not read the table, and it no longer needs to remove contacts first.

The price: contacts loaded by Meshloom are not marked as favorites on the radio, so they can be dropped when a new advert arrives. If you unplug the radio from Meshloom and use it alone, they are no longer protected.

## Channels and slots

The radio has a limited number of channel slots. Meshloom reads the number from the radio and does not assume one.

At startup it empties the channel slots of the radio. Afterwards, sending to a channel reuses the slot it already loaded; a new channel takes a free slot, then replaces the least recently used one when none is left.

Two exceptions to this reuse:

- With a **network (TCP) radio**, every channel send rewrites the channel into the radio, because another program may be using the same radio.
- **Rewrite the channel before every send**, in **Settings → Radio → Advanced**, does the same on every connection type. Use it if the slots seem unstable or if another app changes them. Each channel send then takes a little longer.

## The hourly audit

Meshloom relies on the radio's events to receive messages, and also runs a slow check once an hour. The check looks for messages left on the radio, and for channel slots that no longer match what Meshloom expects.

If it finds a difference, it shows an error and forgets what it believed about the slots. If you see this error, or if messages on the radio never reach Meshloom, **Check for messages every 10 seconds**, in **Settings → Radio → Advanced**, runs the check every 10 seconds instead.

## Hop width: `path_hash_mode`

Every repeater on a route is identified by a few bytes. A longer identifier means fewer mix-ups between repeaters, but fewer repeaters fit in a packet. The setting is **Path Hash Mode**, in the **Configuration** tab of **Settings → Radio**:

| Value | Width per hop | Longest route |
|-------|---------------|---------------|
| `0` | 1 byte | 63 hops |
| `1` | 2 bytes (recommended) | 32 hops |
| `2` | 3 bytes | 21 hops |

When it connects, Meshloom switches a radio that uses 1 byte to 2 bytes, unless you chose to stay on 1 byte. This needs the radio to report its mode while connecting. If it did not (firmware too old to support it, or the radio did not answer), a notice replaces the setting and the radio keeps its current width: reconnect the radio, and if the notice stays, update its firmware.

`path_len`, in the interface and in the API, is **always a number of hops**, never a number of bytes. A channel can have its own width (**Set path hop width override** in its header), applied for that send only.

## Regional flood scope

A message sent by flood travels through every repeater in range. A **region** limits it: repeaters set up for that region forward the message, and repeaters set up to refuse other regions may drop it.

- **Settings → Radio → Messaging → Flood Scope / Region** is the region used for all your sends. Empty means no region (plain flood).
- A channel can use another region, or no region at all, with the globe button in its header. The change applies to the sends to that channel, and then the usual setting is restored. Forcing "no region" when the radio has a default region needs firmware version 12 or newer.

For incoming messages, the region is not written in clear in the packet: it is a code computed with the region's key. Meshloom checks each name in **Known Regions (for decoding)** to find the one that matches. A region missing from the list leaves a message marked as regional but unnamed. When you change the list, Meshloom re-labels the messages whose packet is still stored.

**Discover Regions** asks nearby repeaters which regions they forward, and offers to add them to the list. Only repeaters in direct range answer, and only regions they allow are reported.

## Routing of direct messages

Meshloom picks the route in this order:

1. a route you set by hand for that contact;
2. the route the radio learned;
3. flood.

Learned routes come from the radio's contact list and from path discovery. The route in an advert is shown for information only and is not used for sending. A receipt tells you a message arrived, not how.

A direct message is sent at once. If a receipt is expected and does not come, Meshloom retries up to two times, waiting for the delay the radio suggests. Before the last retry it forgets the stored route, so the last attempt goes by flood even if you had set a route by hand.

## Resending channel messages

**Auto-Resend Unheard Channel Messages**, in **Settings → Radio → Messaging**, resends a channel message once if no repeater echoed it within 2 seconds. The copy is identical, so repeaters that already heard the first one ignore it: no duplicate appears.

## Adverts and location

An advert tells others that you exist. The **Advertising** tab of **Settings → Radio** has:

- **Periodic Advertising Interval**, in hours. `0` turns it off. The minimum is 1 hour (24 or more is recommended), and a shorter value is raised to 1 hour.
- **Send Flood Advertisement**, which goes through repeaters, and **Send Zero-Hop Advertisement**, which stays local and uses less airtime.

The location in the adverts is set by **Advert Location Source**, in the **Configuration** tab. It has only two choices: **Off**, or **Include Node Location**. Companion firmware does not tell a saved position from a live GPS reading. When the radio does not report this setting, Meshloom shows **Off**.

With [Meshloom Community](/en/docs/deep/community/) on, this choice also sets the position Community receives: exact with **Include Node Location**, rounded to about 1 km with **Off**. Changing it, or the radio's position, sends the new one to Community on its own. A radio with no position puts Community on hold until it has one (see [Meshloom Community](/en/docs/deep/community/)).

## GPS and firmware settings

The **Advanced** tab of **Settings → Radio** starts with **Firmware settings**: the settings the radio's own firmware reports. They are stored on the radio and apply at once.

On a radio with a GPS module, the list holds a **GPS** checkbox. When it is on, the radio uses the GPS for its position and sets its clock from it. The firmware looks for the GPS only when the radio starts, so a GPS that is not listed was not connected at that moment: power the radio off, plug the GPS in, then power it on. With **Include Node Location** (see above), the adverts then carry the GPS position.

If the list is empty, the radio reports no setting. Very old firmware reports none at all, and Meshloom says so.

The **Workarounds** group of the same tab holds the fixes mentioned on this page and **Fix a radio clock stuck in the future**, the only one on by default (see [Troubleshooting](/en/docs/deep/troubleshooting/#the-radio-clock-is-stuck-in-the-future)).

## Radio sensors

Below the firmware settings, **Radio sensors** shows what the radio itself reports: its battery voltage, the MCU temperature when the board has a sensor, and the GPS position when the GPS is on. The storage used on the radio is shown too, as used / total KB with a percentage, when the radio reports it. The values are read when you open the tab; **Refresh** reads them again.

## Wi-Fi

Radios with companion firmware 1.18 or later built with Wi-Fi have a Wi-Fi interface that Meshloom reads and sets through the radio's companion command line. That covers the official Bluetooth + Wi-Fi builds and the Wi-Fi builds made by the Meshloom flasher. **Settings → Radio → Connection** then shows a **Wi-Fi** group: the network name (SSID), whether Wi-Fi is on, whether the radio is connected and its address, and **Credentials stored in the radio** (yes or no). The network name shown is the stored one, or the one built into the firmware when nothing is stored: the radio cannot tell which. The group is hidden when the firmware has no command line (older than 1.18, or a radio reached through another Meshloom's radio proxy) or no Wi-Fi.

### The one-time question

After each connection, Meshloom checks whether the radio holds stored Wi-Fi credentials. A Wi-Fi radio built by the flasher joins with credentials compiled into its firmware and has nothing stored. The flasher's "update without wipe" can build firmware without credentials and rely on stored ones: with nothing stored, the radio would never start Wi-Fi again.

So when Wi-Fi is on and nothing is stored, Meshloom asks once for the password, with the network name filled in (you can change it) and an **Open network** option, and says why: this lets the flasher update this radio without retyping Wi-Fi credentials.

- **Store in the radio** sends the network name, then the password, reads both back and checks them. If they do not match, the previous values are put back.
- **Don't ask again for this radio** is remembered for that radio (by its public key): Meshloom never asks again for it. **Store the credentials** in the Wi-Fi group stays available.

The question has no close button and waits for one of these two answers. It waits while a [full backup restore](/en/docs/backup/) runs, since the restore may store the credentials itself, and comes back once the restore ends only if they are still missing. A wrong password, or **Open network** ticked for a protected network, stops the radio joining Wi-Fi after its next reboot. Credentials you type in Meshloom for any other reason are stored in the radio the same way.

### Changing network, turning Wi-Fi on or off

**Change Wi-Fi network** takes the network name and the password twice, or **Open network**. **Turn Wi-Fi off** and **Turn Wi-Fi on** change the radio's setting. Every change applies when the radio restarts. When a change needs a restart, Meshloom asks before rebooting the radio; **Later** keeps the change with **Change pending a reboot** and a **Reboot now** button. Storing the credentials of the network the radio already uses with its built-in credentials needs no restart.

When the radio has no network name at all, **Set up Wi-Fi** replaces **Turn Wi-Fi on**: the radio starts Wi-Fi only once a name is stored. If Wi-Fi is off, **Set up Wi-Fi** turns it on as well, so a single reboot applies both.

When Meshloom reaches the radio over TCP (that is, over its Wi-Fi), the dialogs warn that changing network or turning Wi-Fi off can make the radio unreachable. Reconnect it over USB or Bluetooth if that happens. Once the radio is on Wi-Fi, Meshloom offers to use it over TCP (see [Transports](/en/docs/deep/transports/#from-bluetooth-to-wi-fi)).

### Limits

- A network literally named `(not set)` cannot be used: the radio prints that when no name is set.
- The network name takes 1 to 32 bytes, the password up to 63. Neither is trimmed: spaces count. Below 8 characters Meshloom warns (WPA needs 8, WEP keys have 5 or 13) and stores the password as typed.
- On the radio, an open network reads like "nothing stored". Meshloom remembers that you stored one for that network, and a [full backup](/en/docs/backup/) carries it.
- If storing is interrupted (the link dropped between the two commands), the radio may hold the new network name with the old password or none. Meshloom then shows a warning, even after a reconnect, and offers no reboot (not even with **Turn Wi-Fi on** or **Turn Wi-Fi off**): store the credentials again, or check them over USB or Bluetooth before rebooting the radio.
- If the check after connecting fails (the radio did not answer in time), Meshloom tries again after 30 seconds, then after 2 minutes. **Retry** in the Wi-Fi group checks again. If Meshloom cannot read the Wi-Fi state at all, the group says so, with **Retry**.

### What Meshloom keeps

Meshloom never stores the Wi-Fi password, in its database or in its logs. To know whether one is stored, it reads it back from the radio after each connection (and at each retry of that check) and when it stores credentials, then forgets it. Over TCP it crosses your local network in clear each time. See [Security](/en/docs/deep/security/#wi-fi-password).

## Factory reset

At the very bottom of the **Advanced** tab, **Factory reset** erases the radio. The button needs the radio connected. A window lists what happens, and you must type the radio's name exactly before the confirm button works.

**Erased from the radio:**

- Its identity. A new key pair is generated, so other nodes see a new node.
- Its contacts and channels.
- Its radio settings (frequency, bandwidth, SF, CR, TX power). They go back to the firmware defaults: set them again in the **Radio** tab.
- The Wi-Fi credentials stored in it (see [Wi-Fi](#wi-fi)). A radio reached over Wi-Fi may become unreachable: reconnect it over USB or Bluetooth. The window warns about it when credentials are stored.

**Kept:**

- Meshloom's own database: your messages and contacts.
- Wi-Fi credentials built into the firmware, if any.

The radio restarts and Meshloom reconnects by itself. It then asks whether to adopt the new identity, with the usual identity prompt. This cannot be undone.

## Radio proxy

**Settings → Proxy** can make Meshloom look like a MeshCore radio on your network, so that a phone app or another Meshloom connects through it. It is off by default. When enabled it listens on port 5001, on every network address, for up to 8 clients at a time. You can change the address, the port and the number of clients.

The MeshCore protocol has no password: only enable the proxy on a network you trust. A second Meshloom must use a new database.

## The private key

When it connects, Meshloom asks the radio for its private key and keeps it **in memory only**. It is never written to its database. With it, Meshloom can decrypt direct messages itself, even when the contact is not loaded on the radio, and can read old messages once a key becomes known.

Anyone who reaches the interface can read the key back, through `GET /api/radio/private-key` and in the full backup of **Settings → Database**, which is how you keep the radio's identity across a reflash (see [Back up your radio](/en/docs/backup/)). Firmware built without key export gives no key, and its backups hold no identity. See [Security](/en/docs/deep/security/#the-nodes-private-key).
