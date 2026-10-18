---
title: Plugins
description: What a plugin may read and send, and the limits Meshloom holds it to.
level: deep
order: 22
---

> Draft. Plugins arrive with Meshloom 5.0.0 and replace the Python bots. This page describes their permissions and limits; installing and configuring them will be described when the interface lands.

A plugin runs in its own process, apart from Meshloom. It cannot open your database, your files or the network by itself: it can only ask Meshloom, and Meshloom checks every request against the permissions you granted to that plugin. A request it was not granted is refused before anything happens.

The same plugin can be installed several times (for example one copy per channel). Each copy has its own permissions, settings, limits and stored data.

## Permissions

A plugin lists the permissions it needs. You see them before you install it, and it gets only what both the plugin asks for and you accept.

| Permission | What the plugin can do |
|---|---|
| Read messages | Receive the messages Meshloom hears. By default only **public channels**: the Public channel and the `#` channels anyone can join. It may also ask for **every channel** (private ones included), your **direct messages**, and the **messages you send**. |
| Send messages | Send from your radio, under your name. By default **only replies**, in the conversation of the message it answers. It may ask to send to **the channels and contacts it may read**, or **anywhere**: only allow that for a plugin you trust. |
| Read radio packets | Receive the raw packets. It sees what they decrypt to only for the messages it may read. |
| Read contacts | See the names, public keys, last activity and positions of the nodes you know. |
| List channels | See the names and keys of the public channels, and of private ones when it may read every channel. A channel key lets anyone read that channel. |
| Read telemetry | Receive the telemetry of your nodes and the health of Meshloom. |
| Store data | Keep up to 5 MB of its own data in Meshloom. |
| Run on a schedule | Wake itself up later, no sooner than 10 seconds after asking. |
| Connect to the internet | Ask Meshloom to contact the servers it lists, and only those (see below). |

You can narrow what a plugin reads further by picking the channels and contacts it may see, as for an integration in **MQTT & Automation**.

A plugin never receives its own messages back unless it was allowed to read the messages you send.

### Connecting to the internet

A plugin never opens a connection itself. When it says it **contacts `api.example.com`**, Meshloom makes the request for it, and only to the exact names listed (`api.example.com` does not cover `www.example.com` or another port). Meshloom also checks that:

- the address is encrypted (`https://`);
- the name does not lead to your local network (your router, a NAS, `localhost`), whatever it resolves to;
- a redirect only goes to another listed name, three times at most; a password or API key the plugin sends does not follow a redirect to another server;
- the request carries no cookie and nothing of Meshloom's own (no account, no session);
- each answer is cut at 1 MB, a request sends at most 256 KB, and a request lasts at most 10 seconds;
- a copy of a plugin makes at most 60 requests per hour.

Servers under `meshloom.app` are reserved to Meshloom's own plugins. Meshloom's log shows each request with its address, but never the part after `?`, where API keys usually sit.

Plugins that **may talk to a device on your network** (a home automation box, a printer) will come later: you will then accept each device by name. Until then a plugin cannot reach your local network at all.

Combined with reading private channels or direct messages, this permission lets a plugin send them to the servers it lists: only allow both for a plugin you trust.

## Limits

- **Messages sent.** By default 30 per hour and 200 per day for each copy of a plugin. A plugin may ask for more, up to 60 per hour and 500 per day, and you can always lower it. The counters survive a restart of Meshloom.
- **Spacing.** Meshloom waits at least 2 seconds between two automatic messages, whichever plugin or bot sends them, so repeaters have time to listen again.
- **Size.** A message is limited to what the radio can carry: 156 bytes for a direct message, less on a channel, where your node's name comes first.
- **Regions.** A plugin can scope a channel message only to a region Meshloom already knows.
- **Storage.** 5 MB per copy of a plugin.
- **Internet.** 60 requests per hour per copy of a plugin, 10 seconds each, answers cut at 1 MB. This counter starts again when Meshloom restarts.

A refused request shows up in the plugin's log with its reason, such as "not allowed", "limit reached", "message too long" or "server not allowed".
