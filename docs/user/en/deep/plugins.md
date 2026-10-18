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
- **Spacing.** Meshloom waits at least 2 seconds between two automatic messages, whichever plugin or bot sends them, so repeaters have time to listen again.
- **Size.** A message is limited to what the radio can carry: 156 bytes for a direct message, less on a channel, where your node's name comes first.
- **Regions.** A plugin can scope a channel message only to a region Meshloom already knows.
- **Storage.** 5 MB per copy of a plugin.

A refused request shows up in the plugin's log with its reason, such as "not allowed", "limit reached" or "message too long".

## Isolation

The operating system puts walls around each plugin's process. How strong the walls are depends on how you installed Meshloom. The level is decided when the server starts, written in the server log, and reported by `/api/health` in `plugins_isolation`.

### Isolated by bubblewrap (`bwrap`)

The Linux package, the Raspberry Pi image and a source install on Linux use [bubblewrap](https://github.com/containers/bubblewrap). The plugin sees only its own files, the Python it runs on and a private scratch folder. It cannot see the database, the rest of Meshloom's files or the server's memory, and it has no network at all: anything it fetches goes through Meshloom, which checks the plugin's permissions. Stopping the plugin stops everything it started.

### Dedicated user (`setuid`)

The Docker image and the Home Assistant add-on run Meshloom as root, and Docker does not allow bubblewrap. Plugins run as a separate account, `meshloom-plugin`, that cannot open the database or read the server's memory. To make that true, Meshloom tightens the permissions of its data folder at startup: the folder becomes `0711` and the files in it `0600` (readable by root only); the plugin folders stay readable. If you open the database from the host with another account, do it as root. At this level a plugin that breaks out of Meshloom's own guards could still reach the network.

### Hooks only (`none`)

Neither of the above is available, so only Meshloom's own guards inside the plugin process remain. They stop honest mistakes, not a determined attacker: a hostile plugin could read the database and Meshloom's files. The server log says so with a warning at every start. This happens when:

- bubblewrap is not installed (the Linux package recommends it; `sudo apt install bubblewrap` or `sudo dnf install bubblewrap` adds it);
- the system refuses the user namespaces bubblewrap needs (Ubuntu 24.04 and later restrict them; a hardened kernel may disable them);
- the Docker image runs with `MESHLOOM_RUN_AS_USER`, which gives up the rights needed to switch to the plugin account;
- Meshloom runs on macOS or Windows.

### Check your level

Look for `Plugin isolation` in the server log at startup, or open `/api/health` while signed in and read `plugins_isolation`: `bwrap`, `setuid` or `none`. After installing bubblewrap, restart Meshloom so it checks again.

Escaping these walls is a security bug: report it as described in the project's security policy. Plugins you install are still code you choose to trust; the walls limit what a mistake or a bad update can reach.
