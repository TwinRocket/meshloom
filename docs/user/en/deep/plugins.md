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

You can narrow what a plugin reads further by picking the channels and contacts it may see, as for an integration in **MQTT & Automation**.

A plugin never receives its own messages back unless it was allowed to read the messages you send.

## Limits

- **Messages sent.** By default 30 per hour and 200 per day for each copy of a plugin. A plugin may ask for more, up to 60 per hour and 500 per day, and you can always lower it. The counters survive a restart of Meshloom.
- **All plugins together.** Whatever the copies are allowed individually, together they send at most 120 messages per hour from your Meshloom. Each channel receives at most 20 messages per hour from plugins and each contact 10 per hour, counted across all plugins. You can lower these three limits, never raise them. The counters survive a restart of Meshloom.
- **Loops.** A plugin copy answers the same sender at most once every 30 seconds, and does not answer a message that arrived less than 5 seconds after it sent one to the same channel or contact. This stops two auto-responders, even on two different Meshloom, from answering each other forever.
- **Spacing.** Meshloom waits at least 2 seconds between two automatic messages, whichever plugin or bot sends them, so repeaters have time to listen again.
- **Size.** A message is limited to what the radio can carry: 156 bytes for a direct message, less on a channel, where your node's name comes first.
- **Regions.** A plugin can scope a channel message only to a region Meshloom already knows.
- **Storage.** 5 MB per copy of a plugin.

A refused request shows up in the plugin's log with its reason, such as "not allowed", "limit reached" or "message too long". For the limits above, the reason says which one fired: the copy's own limit, all plugins together, the channel or contact, the sender delay or the quiet delay.
