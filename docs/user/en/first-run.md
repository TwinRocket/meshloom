---
title: First launch
description: Open the interface, check the radio, name the node, and look around.
level: start
order: 3
---

The server is running. Open the page, bind the radio, then look around.

```
http://127.0.0.1:8000
```

From another device on the same network, use the machine’s IP address with port `8000`.

## Bind the radio

Transport — USB, TCP, or Bluetooth — is chosen in the web UI, not during install. Until it is set, the status bar stays paused and **Connect** opens **Settings > Radio**.

Pick the transport, enter the port, host, or BLE address and PIN, then apply. Details are in [Radio transports](/en/docs/deep/transports/).

The bar then moves through **Radio connecting**, **Radio initializing**, and **Radio OK** when the link is up and synchronization is complete. The first connection takes a moment: the server reads the device configuration, retrieves its contacts and channels, and sets its clock.

If the bar says **Radio disconnected**, a **Reconnect** button appears beside it. A server that cannot find the radio retries by itself every few seconds, so reconnecting a cable or powering up the radio is usually enough. If the state does not change: wrong serial port, wrong IP address, or rejected Bluetooth PIN. See [Troubleshooting](/en/docs/deep/troubleshooting/).

## First identity bind

Meshloom binds the database to the radio’s public key. A new install does this quietly. After upgrading an existing database, a dialog asks you to confirm.

- **Bind without wiping** if this is the same radio: contacts and messages stay.
- **New radio** if this is a different device: local mesh contacts and messages are wiped. Channels and server settings stay.

**Previous key: unknown** means the database predates identity binding, not that the radio changed. If the new key is yours, bind without wiping.

If a different radio is connected, the dialog shows both key prefixes. Bind-without-wipe is not offered: adopting the other identity requires a wipe.

The status bar also shows the node name, its public key — click it to copy — and the battery level when the radio provides it.

## Name the node

A node without a name appears to other people under the first characters of its public key. Give it a name.

Open **Settings**, then **Radio**, the **Identity** group, and the **Radio name** field. This name goes out in every advert and is what other people see in their contact list. Short is better: radio packets have limited space.

The same section contains radio settings: presets, frequency, bandwidth, and the rest. They must match nearby nodes or nobody will hear anybody else. If the node stays silent while other devices nearby are active, this is the first place to look. It is worth knowing the local settings before changing them.

## Announce yourself

Farther down in the same section is **Adverts and discovery**. An advert is the small packet a node sends to say that it exists, with its name and public key.

- **Send advert** sends one immediately. The *flood* version travels through repeaters and goes farther. The *zero-hop* version stays local and uses much less airtime.
- **Periodic advert interval** controls automatic repeats. `0` disables them. The minimum is one hour, and twenty-four hours or more is recommended: adverts sent too often occupy airtime for everyone.

The reverse works too. Contacts do not have to be entered by hand: every advert heard creates or updates a contact automatically. An empty list at startup is not a problem; it just means the network has not been heard yet.

## Look around

On a phone, the bottom bar offers four destinations: **Conversations**, **Map**, **Tools**, and **Settings**. On a large screen, an icon rail on the left offers **Conversations** and the **Map**, then the tools; **Settings** stays at the bottom of the rail.

The conversation list is grouped into sections: **Favorites**, **Channels**, **Contacts**, **Repeaters**, and **Room servers**. A **Public** channel exists from the start; it is MeshCore’s default channel, open to everyone.

Tools open from the **Tools** screen, or from an icon on the rail:

- **Packet feed** — everything the radio hears, whether it can be decrypted or not.
- **Control journal** — requests, responses, and group data, kept apart from conversations.
- **Live** — packets placed on a map as they are heard.
- **Mesh visualizer** — the paths packets actually took.
- **Trace** — a route test to one node.
- **RF locate** — an estimated coverage area, not a GPS point.
- **Message search** — full-text search across the history.
- **Discovered channels** — hashtag channels that were heard, to keep or dismiss.
- **Radio test** — sends a test message and shows who heard it. It is offered only when Meshloom Community is on.

The **Add channel/contact** button opens conversation creation: a contact by public key, a channel by key, or a hashtag channel by name.

A new install shows a **Community** banner until an airport IATA code is saved under **Settings > Community**. You can leave Community from that pane. See [Meshloom Community](/en/docs/deep/community/).

**Settings > Notifications** is where Web Push lives (this device, defaults, exceptions, VAPID subject). There are no in-tab desktop alerts.

To send something: [Messages](/en/docs/messages/).
