---
title: First launch
description: Open the interface, connect the radio, name the node, and look around.
level: start
order: 3
---

The server is running. Now open the page, connect the radio, and look around.

```
http://127.0.0.1:8000
```

From another device on the same network, use the machine's IP address with port `8000`.

## Language of the interface

The interface opens in French by default. To switch to English, open **Settings > Local Configuration** and change **Language**. The choice applies to this browser only. This guide uses the English names of buttons and menus.

## Connect the radio

Meshloom does not know yet how to reach your radio. The link (USB, network or Bluetooth) is chosen in the interface, not during installation. Until it is set, a **No radio connected** banner is shown and the status bar says **Radio Paused**.

1. Press **Connect a radio** in the banner, or **Connect** in the status bar. This opens **Settings > Radio**.
2. In **Transport**, choose **Serial** (USB cable), **TCP** (radio on the network) or **Bluetooth**.
3. Fill in the field that appears: the serial port (leave **Auto-detect** if only one radio is plugged in), the host and port, or the Bluetooth address and PIN.
4. Press **Save and connect**.

[Radio transports](/en/docs/deep/transports/) explains each field.

The status bar then moves through **Radio Connecting**, **Radio Initializing** and **Radio OK**. **Radio OK** means the link is up and everything is synchronized. The first connection takes a moment: Meshloom reads the radio's configuration, retrieves its contacts and channels, and sets its clock.

If the bar says **Radio Disconnected**, a **Reconnect** button appears. Meshloom also retries by itself every few seconds, so plugging the cable back in or powering up the radio is usually enough. If nothing changes, the usual causes are a wrong serial port, a wrong IP address or a wrong Bluetooth PIN. See [Troubleshooting](/en/docs/deep/troubleshooting/).

Click the radio status in the bar for a summary of the connection.

## First identity check

Meshloom ties its database to the public key of your radio, so that the history of two radios is never mixed. On a new install this happens silently. After upgrading a database that already contains data, a dialog asks you to confirm.

- Choose **Bind without wipe** if it is the same radio. Contacts and messages stay.
- Choose **New radio** if it is another device. Mesh contacts, messages, stored packets and telemetry history are erased. Channels and settings stay.

**Previous key: Unknown** only means the database is older than this check. It does not mean the radio changed. If the key shown is yours, choose **Bind without wipe**.

If a radio with a different key is connected, the dialog shows both keys and offers **Cancel** or **Wipe and continue**. Keeping the history is not offered, because that history belongs to the other radio.

Once connected, the status bar also shows the node's name, its public key (click it to copy it), and the battery level when the radio reports it.

## Name your node

A node without a name appears to other people as the first characters of its public key. Give yours a name.

1. Open **Settings > Radio**.
2. In the **Identity** group, fill in **Radio Name**.
3. Save.

This name goes out with every advert and is what others see in their contact list. Keep it short: on a channel, your name is sent with every message and uses part of its 156 bytes.

The same page holds the radio parameters (preset, frequency, bandwidth and so on). They must match those of the nodes around you, or nobody will hear anybody. If your node stays silent while others nearby are active, check them first, and note the current values before changing anything.

## Announce yourself

An advert is a small packet that says "I am here", with the node's name and public key. In **Settings > Radio**, the **Advertising & Discovery** group controls it.

- **Send Flood Advertisement** goes out through the repeaters and travels far.
- **Send Zero-Hop Advertisement** stays local and uses much less airtime.
- **Periodic Advertising Interval** sends adverts automatically. `0` turns this off. The minimum is 1 hour, and 24 hours or more is recommended: too many adverts clog the airwaves for everyone.

The status bar panel (click the radio status) also has quick **Flood** and **Zero-hop** buttons.

The reverse works too: you do not enter contacts by hand. Every advert Meshloom hears creates or updates a contact. An empty list on the first day just means the network has not been heard yet. You can ignore some kinds of nodes in **Settings > Radio-App Management**, with **Block Discovery of New Node Types** (clients, repeaters, room servers, sensors).

## Look around

On a phone, the bottom bar has four entries: **Chats**, **Map**, **Tools** and **Settings**. On a large screen, a bar of icons on the left shows the chats, the map and each tool, with **Settings** at the bottom. You can choose which tools stay on that bar, and in which order, in **Settings > Navigation**.

The conversation list is split into sections: **Favorites**, **Channels**, **Contacts**, **Repeaters** and **Room Servers**. A **Public** channel exists from the start. It is MeshCore's default channel, open to everyone.

The **Tools** screen (or the icon bar) opens:

- **Packet Feed**: every packet the radio hears, whether or not it can be decrypted.
- **Control journal**: requests, replies and group data, kept apart from conversations.
- **Live**: packets appear on a map as they are heard.
- **Mesh Visualizer**: the paths packets actually took.
- **Trace**: a route test through chosen repeaters.
- **RF Locate**: an estimated coverage zone, not a GPS point.
- **Message Search**: search the whole history.
- **Discovered channels**: hashtag channels Meshloom found on its own. You **Adopt** or **Refuse** each one.
- **Radio test**: sends a test message and shows who heard it. It only appears when Meshloom Community is on.

The **Add Channel/Contact** button creates a conversation: a contact from its public key, a private channel from its key, a hashtag channel from its name, or several hashtag channels at once.

New installs join [Meshloom Community](/en/docs/deep/community/). Until you enter the code of the nearest airport (its three-letter IATA code) in **Settings > Meshloom Community**, a banner reminds you and nothing is published. You can also leave Community from that page.

Other useful pages in **Settings**: **Notifications** (below), **Updates**, **MQTT & Automation** (outputs and bots), **Alerts** (telemetry thresholds), **Database** (size and storage cleanup), **Statistics** and **About**.

**Settings > Notifications** is where you choose what Meshloom tells you about (new contacts, direct messages, new repeaters or sensors, found channels, telemetry alerts, updates), and where: Web Push on this browser, email, or a webhook. Push also works when the browser is closed, but needs HTTPS. Meshloom shows no pop-up alerts inside the open tab. See [Push notifications](/en/docs/deep/push/).

To send your first message: [Messages](/en/docs/messages/).
