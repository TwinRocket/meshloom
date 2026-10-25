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

A plugin may also ask to **talk to a device on your network** (a home automation box, a printer). The consent then lists each device by name (`hue-bridge.local`, `192.168.1.20:8080`...). Meshloom reaches only those devices, on the listed port, possibly without encryption (`http://`); the name must lead to an address of your local network every time it is looked up, a redirect may only go to another listed device or server, and every other rule above still applies. Only accept a device you know: the plugin can then talk to it as freely as you can.

Combined with reading private channels or direct messages, this permission lets a plugin send them to the servers it lists: only allow both for a plugin you trust.

## Limits

- **Messages sent.** By default 30 per hour and 200 per day for each copy of a plugin. A plugin may ask for more, up to 60 per hour and 500 per day, and you can always lower it. The counters survive a restart of Meshloom.
- **All plugins together.** Whatever the copies are allowed individually, together they send at most 120 messages per hour from your Meshloom. Each channel receives at most 20 messages per hour from plugins and each contact 10 per hour, counted across all plugins. These three limits can be lowered, never raised; the control for it comes with the plugin settings screen (a later release). Plugins only: the bots keep their own 2-second spacing and are not counted. The counters survive a restart of Meshloom.
- **Loops.** A plugin copy answers the same sender at most once every 30 seconds, and does not answer a message that arrived less than 5 seconds after it sent one to the same channel or contact. This stops two auto-responders, even on two different Meshloom, from answering each other forever.
- **Spacing.** Meshloom waits at least 2 seconds between two automatic messages, whichever plugin or bot sends them, so repeaters have time to listen again.
- **Size.** A message is limited to what the radio can carry: 156 bytes for a direct message, less on a channel, where your node's name comes first.
- **Regions.** A plugin can scope a channel message only to a region Meshloom already knows.
- **Storage.** 5 MB per copy of a plugin.
- **Internet.** 60 requests per hour per copy of a plugin, 10 seconds each, answers cut at 1 MB. This counter starts again when Meshloom restarts.

A refused request shows up in the plugin's log with its reason, such as "not allowed", "limit reached", "message too long" or "server not allowed". For the limits above, the reason says which one fired: the copy's own limit, all plugins together, the channel or contact, the sender delay or the quiet delay.

## Isolation

The operating system puts walls around each plugin's process. How strong the walls are depends on how you installed Meshloom. The level is decided when the server starts, written in the server log, and reported by `/api/health` in `plugins_isolation`.

### Isolated by bubblewrap (`bwrap`)

The Linux package, the Raspberry Pi image and a source install on Linux use [bubblewrap](https://github.com/containers/bubblewrap). The plugin sees only its own files, the Python it runs on and a private scratch folder. It cannot see the database, the rest of Meshloom's files or the server's memory, and it has no network at all: anything it fetches goes through Meshloom, which checks the plugin's permissions. Stopping the plugin stops everything it started.

### Dedicated user (`setuid`)

The Docker image and the Home Assistant add-on run Meshloom as root, and Docker does not allow bubblewrap. Plugins run as a separate account, `meshloom-plugin`, that cannot open the database or read the server's memory. To make that true, Meshloom tightens the permissions of its data folder at startup: the folder becomes `0711` and the files in it `0600` (readable by root only); the plugin folders stay readable. If you open the database from the host with another account, do it as root. At this level a plugin that breaks out of Meshloom's own guards could still reach the network, including Meshloom's radio proxy on the same machine, through which it could send more messages than its limits allow. The account has the fixed uid and gid 10002.

### Hooks only (`none`)

Neither of the above is available, so only Meshloom's own guards inside the plugin process remain. They stop honest mistakes, not a determined attacker: a hostile plugin could read the database and Meshloom's files. The server log says so with a warning at every start. This happens when:

- bubblewrap is not installed (the Linux package recommends it; `sudo apt install bubblewrap` or `sudo dnf install bubblewrap` adds it);
- the system refuses the user namespaces bubblewrap needs (Ubuntu 24.04 and later restrict them; a hardened kernel may disable them);
- the Docker image runs with `MESHLOOM_RUN_AS_USER`, which gives up the rights needed to switch to the plugin account;
- Meshloom runs on macOS or Windows.

### Check your level

Look for `Plugin isolation` in the server log at startup, or open `/api/health` while signed in and read `plugins_isolation`: `bwrap`, `setuid` or `none`. After installing bubblewrap, restart Meshloom so it checks again.

Escaping these walls is a security bug: report it as described in the project's security policy. Plugins you install are still code you choose to trust; the walls limit what a mistake or a bad update can reach.

## Settings → Extensions (draft)

Administrators manage plugins in **Settings → Extensions** (the interface calls them extensions). The top of the page shows the isolation level in force and what it means. The **Installed** tab lists each copy with its status, a badge when it needs attention (settings required, permissions to review, consent needed), whether it is bundled, an on/off switch and arrows to reorder. **Store** and **Updates** are placeholders for now.

- **Add a bundled extension** shows what it may do in plain words, the servers it talks to and its privacy declaration. When it can read private messages (direct messages or every channel) and reach the internet, a warning says it can send your private messages to those servers. Nothing is installed until you tick the box and confirm.
- A **revoked** or **incompatible** extension cannot be turned on. One that needs a review stays off until you open it and choose **Accept and enable**.
- The detail drawer lets you untick permissions, choose the conversations it may read, lower its sending limits (never above what it asks for), read its log, restart it, **copy a diagnostic** for a bug report (no setting values, no secrets) and uninstall it, keeping its stored data or not.
- The **Settings** block of the drawer is generated from the extension's own description of its settings (required fields are starred). A secret setting (an API key, a password) is never shown, not even after you save it: the drawer only says a value is set, and you can **Replace** it (leave the field empty to keep the current value) or **Clear** it. Only what you change is sent. An extension waiting for required settings stays off; its drawer has a **Go to the settings** button.

### Local extensions

**New local extension** (in the Installed tab) creates a small Python program kept in Meshloom, from a starting code (an echo that answers `!pling` with `Plong!`, or an empty one). It starts off, with only the permissions you give it. In its drawer:

- **Code**: edit it in the editor and **Save code**. The code is read, never run, when you save: lines worth a look (a module that is not allowed, `print`, a value lost at restart...) are listed with their line number, and a server named in the code (`https://...`) is offered with an **Allow** button that adds it to the servers the extension may contact.
- **Events and permissions**: the events it listens to and what it may do, changed and saved from the same place.
- **Check** reads the manifest and scans the code without running anything; errors and warnings come with their line.
- **Test with a message** sends a made-up message to the running extension (from a test channel, one of your channels or a contact) and shows what it answered and its log. **Nothing is sent to the radio**: the answers are captured. Save the code first, and turn the extension on to test it.
- The log under the drawer shows what the extension printed, so you can see errors without a command line.
