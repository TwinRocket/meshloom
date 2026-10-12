---
title: Radio, contacts, and channels
description: What Meshloom loads onto the radio, flood scope, and hop width.
level: deep
order: 15
---

Meshloom manages the contacts and channels stored on your radio. The server remembers far more than the radio can hold, and loads onto the radio the working set it needs.

The database is tied to the public key of your radio. If you plug in a different radio, a window opens: **This radio does not match the stored identity**. **Wipe and continue** adopts the new radio and erases the local mesh contacts and messages; channels and server settings stay. **Cancel** keeps your data untouched and pauses the connection to that radio. A database created before this link existed asks once, even for its usual radio: **This radio is not bound to this instance**. Choose **Bind without wipe** if it is the same radio, or **New radio** to start clean. See [First launch](/en/docs/first-run/).

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

`MESHCORE_LOAD_WITH_AUTOEVICT=true` makes the radio drop its oldest non-favorite contact by itself when the table is full. Adding contacts then never fails, Meshloom can load contacts even if it could not read the table, and it no longer needs to remove contacts first.

The price: contacts loaded by Meshloom are not marked as favorites on the radio, so they can be dropped when a new advert arrives. If you unplug the radio from Meshloom and use it alone, they are no longer protected.

## Channels and slots

The radio has a limited number of channel slots. Meshloom reads the number from the radio and does not assume one.

At startup it empties the channel slots of the radio. Afterwards, sending to a channel reuses the slot it already loaded; a new channel takes a free slot, then replaces the least recently used one when none is left.

Two exceptions to this reuse:

- With a **network (TCP) radio**, every channel send rewrites the channel into the radio, because another program may be using the same radio.
- `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE=true` does the same on every connection type. Use it if the slots seem unstable or if another app changes them. Each channel send then takes a little longer.

## The hourly audit

Meshloom relies on the radio's events to receive messages, and also runs a slow check once an hour. The check looks for messages left on the radio, and for channel slots that no longer match what Meshloom expects.

If it finds a difference, it shows an error and forgets what it believed about the slots. If you see this error, or if messages on the radio never reach Meshloom, `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK=true` runs the check every 10 seconds instead.

## Hop width: `path_hash_mode`

Every repeater on a route is identified by a few bytes. A longer identifier means fewer mix-ups between repeaters, but fewer repeaters fit in a packet. The setting is **Path Hash Mode**, in the **Configuration** tab of **Settings → Radio**:

| Value | Width per hop | Longest route |
|-------|---------------|---------------|
| `0` | 1 byte | 63 hops |
| `1` | 2 bytes (recommended) | 32 hops |
| `2` | 3 bytes | 21 hops |

When it connects, Meshloom switches a radio that uses 1 byte to 2 bytes, unless you chose to stay on 1 byte. The setting only appears if the radio's firmware supports it.

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

The location in the adverts is set by **Advert Location Source**, in the **Configuration** tab. It has only two choices: **Off**, or **Include Node Location**. Companion firmware does not tell a saved position from a live GPS reading.

## Radio proxy

**Settings → Proxy** can make Meshloom look like a MeshCore radio on your network, so that a phone app or another Meshloom connects through it. It is off by default. When enabled it listens on port 5001, on every network address, for up to 8 clients at a time. You can change the address, the port and the number of clients.

The MeshCore protocol has no password: only enable the proxy on a network you trust. A second Meshloom must use a new database.

## The private key

When it connects, Meshloom asks the radio for its private key and keeps it **in memory only**. It is never written to disk. With it, Meshloom can decrypt direct messages itself, even when the contact is not loaded on the radio, and can read old messages once a key becomes known.

Exporting this key through the API is off by default. See [Security](/en/docs/deep/security/).
