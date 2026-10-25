---
title: Plugins
description: What a plugin (extension) is, where it runs, what it may read and send, the limits it is held to, and what leaves your instance.
level: deep
order: 22
---

A plugin (the interface calls it an **extension**) is a small Python program that reacts to what Meshloom hears and may answer. Plugins arrived with Meshloom 5.0.0 and replaced the Python bots, which were retired then (see [Bots from 4.18](#bots-from-418-and-earlier)). Administrators manage them in **Settings → Extensions** (see [below](#settings--extensions)). This page says where they run, what they may do and what limits hold them.

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

A plugin may also ask to **talk to a device on your network** (a home automation box, a printer). The consent then lists each device by name (`hue-bridge.local`, `192.168.1.20:8080`...). Only local names (`nas`, `*.local`, `*.lan`, `*.home.arpa`, `*.internal`) and local addresses are accepted, including those of a VPN such as Tailscale (`100.x.y.z`); an IPv6 address with a port is written in brackets (`[fd00::1]:80`). Meshloom reaches only those devices, on the listed port, possibly without encryption (`http://`) or without checking a self-signed certificate; it refuses the machine Meshloom itself runs on. Inside a container (Docker, Home Assistant add-on) Meshloom cannot see that machine's own address on your network, so a plugin that lists it can still reach it, radio proxy included: do not accept it. In the Home Assistant add-on the name `homeassistant` is refused on purpose: it leads to the Supervisor's gateway, which is the Home Assistant machine itself (its API and Meshloom's radio proxy). The add-on refuses the Supervisor's whole internal network (`172.30.32.0/23`) the same way: the Supervisor and the other add-ons are not devices of your network. A plugin that lists such an address is invalid (Meshloom names the reason when you try to add it), and a name that leads there is refused. If an already installed plugin lists one, it loses its whole network permission, public sites included, and its details show a notice; remove that address from the plugin to restore it. Outside the add-on (Linux package, Docker), a local network numbered in that range is still accepted. The name must lead to an address of your local network every time it is looked up, a redirect may only go to another listed device or server, and every other rule above still applies. A `.local` name is looked up by the system Meshloom runs on, which does not always know how: in Docker (bridge or host network) and possibly in the Home Assistant add-on, `nas.local` may not be found and the plugin then fails with an unresolvable-name error. In a container, prefer the device's IP address (`192.168.1.20`), and give the device a fixed address in your router (DHCP reservation) so it does not change. Only accept a device you know: the plugin can then talk to it as freely as you can.

Combined with reading private channels or direct messages, this permission lets a plugin send them to the servers it lists: only allow both for a plugin you trust.

## Limits

- **Messages sent.** By default 30 per hour and 200 per day for each copy of a plugin. A plugin may ask for more, up to 60 per hour and 500 per day, and you can always lower it. The counters survive a restart of Meshloom.
- **All plugins together.** Whatever the copies are allowed individually, together they send at most 120 messages per hour from your Meshloom. Each channel receives at most 20 messages per hour from plugins and each contact 10 per hour, counted across all plugins. These three limits are fixed in 5.0.0: the code can only ever lower them, and no screen or setting does so yet. The counters survive a restart of Meshloom.
- **Loops.** A plugin copy answers the same sender at most once every 30 seconds, and does not answer a message that arrived less than 5 seconds after it sent one to the same channel or contact. This stops two auto-responders, even on two different Meshloom, from answering each other forever.
- **Spacing.** Meshloom waits at least 2 seconds between two automatic messages, whichever plugin sends them, so repeaters have time to listen again.
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

The Docker image and the Home Assistant add-on run Meshloom as root, and Docker does not allow bubblewrap. Each copy of a plugin runs under an account of its own (a number between 40000 and 49999, kept for as long as the copy exists), which cannot open the database, read the server's memory, or see or stop another copy. To make that true, Meshloom tightens the permissions of its data folder at startup: the folder becomes `0711` and the files in it `0600` (readable by root only); the plugin folders stay readable. If you open the database from the host with another account, do it as root. At this level a plugin that breaks out of Meshloom's own guards could still reach the network, including Meshloom's radio proxy on the same machine, through which it could send more messages than its limits allow. This level needs the `meshloom-plugin` account (uid and gid 10002 in the image), which the image creates; no plugin runs under it. When you delete a copy, Meshloom removes the temporary files its account left and frees the number. If a folder in `/tmp` (or another temporary folder) that Meshloom may enter but not list stands in the way, the number stays taken, for safety: Settings → Extensions then names the folder (administrators only, `plugins_purge_blocked` in `/api/health`). Remove that folder or make it readable (`chmod o+r`), then restart Meshloom. If the path is in a `.meshloom-plugin-uids` folder, another Meshloom on the same machine holds the number (see [Other install paths](/en/docs/deep/install-paths/)). With `MESHLOOM_RUN_AS_USER`, Meshloom keeps only the two rights it needs to run plugins under their own accounts, so plugins stay at this level; the data folder then belongs to the account you chose.

### Hooks only (`none`)

Neither of the above is available, so only Meshloom's own guards inside the plugin process remain. They stop honest mistakes, not a determined attacker: a hostile plugin could read the database and Meshloom's files. The server log says so with a warning at every start. This happens when:

- bubblewrap is not installed (the Linux package recommends it; `sudo apt install bubblewrap` or `sudo dnf install bubblewrap` adds it);
- the system refuses the user namespaces bubblewrap needs (Ubuntu 24.04 and later restrict them; a hardened kernel may disable them);
- Meshloom runs on macOS (on Windows there are no plugins at all, see below).

### Check your level

Look for `Plugin isolation` in the server log at startup, or open `/api/health` while signed in and read `plugins_isolation`: `bwrap`, `setuid` or `none`. After installing bubblewrap, restart Meshloom so it checks again.

**Windows.** Plugins need the operating system's resource limits, which Windows does not have: there, the plugin system is switched off, nothing can be installed or started, and `/api/health` reports `plugins_disabled_source: "platform"`.

Escaping these walls is a security bug: report it as described in the project's security policy. The hooks-only level is explicitly weaker and is not a promise of isolation. Plugins you install are still code you choose to trust; the walls limit what a mistake or a bad update can reach.

## Settings → Extensions

Administrators manage plugins in **Settings → Extensions** (the interface calls them extensions). The top of the page shows the isolation level in force and what it means. The **Installed** tab lists each copy with its status, a badge when it needs attention (settings required, permissions to review, consent needed), whether it is bundled, an on/off switch and arrows to reorder. **Store** and **Updates** are placeholders for now.

- **Add a bundled extension** lists the extensions that come with Meshloom (see below), works without internet, and shows what it may do in plain words, the servers it talks to and its privacy declaration. When it can read private messages (direct messages or every channel) and reach the internet, a warning says it can send your private messages to those servers. Nothing is installed until you tick the box and confirm. This consent covers every permission the extension asks for: Meshloom refuses to install it with less and names what is missing. Once it is installed, you can untick permissions in its drawer.
- A **revoked** or **incompatible** extension cannot be turned on. One that needs a review stays off until you open it and choose **Accept and enable**.
- The detail drawer lets you untick permissions, choose the conversations it may read, lower its sending limits (never above what it asks for), read its log, restart it, **copy a diagnostic** for a bug report (no setting values, no secrets) and uninstall it, keeping its stored data and secret settings or not. The extension's code is removed with its last copy.
- The **Settings** block of the drawer is generated from the extension's own description of its settings (required fields are starred). A secret setting (an API key, a password) is never shown, not even after you save it: the drawer only says a value is set, and you can **Replace** it (leave the field empty to keep the current value) or **Clear** it. Only what you change is sent. An extension waiting for required settings stays off; its drawer has a **Go to the settings** button.

### Bundled extensions

Two extensions ship with Meshloom and install without any network:

- **Echo** answers `!pling` with `Plong!`. It reads public channels and your direct messages and only replies. Its code is the reference for writing your own.
- **Auto-responder** answers messages from rules you write, with no code. One rule per line, in its **Rules** setting: `trigger: text => reply`. The trigger is `keyword` (the word appears in the message), `prefix` (the message starts with it) or `regex` (a pattern of at most 100 characters, in a restricted dialect: at most two repeats of more than 5 times, no repeated group holding a repeat or a choice, no back-reference or lookaround; it only reads the first 200 characters of the message). Add options after the trigger, separated by commas: `channel=#name` (or `channel=dm`) to limit a rule to one channel, `cooldown=300` for a pause in seconds before answering the same person again. The reply may contain `{sender}`, `{channel}` and `{text}`. For example `prefix,channel=#test: !ping => Pong, {sender}!`. The first rule that matches decides; a rule that cannot be read is skipped and written to the log. Other settings: respect upper and lower case, answer direct messages or not, the default pause (120 seconds) and the most answers per hour (20). Beyond them, the limits above always apply, including one answer every 30 seconds to the same person.

#### When Meshloom is upgraded

At start, Meshloom compares each bundled extension you installed with the version that ships in the new release and updates the newer ones **in place**: the code changes, while the name, settings, secrets, conversations it may read, limits and stored data stay. If the new version asks for no new permission, it keeps running. If it asks for different permissions (a new one, or a changed one, even narrower) or its privacy declaration says more (a new third-party server, or it now stores message content), it is updated but **held**: it reads *Needs attention* with *Review permissions* (*Privacy changed* when only its privacy declaration changed), and stays off until you open it, read what it may now do and accept. Permissions it already had stay granted. An extension that cannot be updated (a damaged package, a full disk) keeps its old version, the reason is written to the log and Meshloom starts normally. In the rare case where its old version could not be put back either, it shows an error saying its code does not match its version and stays off until the next restart of Meshloom finishes the update. Nothing is updated while extensions are disabled by the host (the Home Assistant add-on's `disable_plugins` option), and a revoked or incompatible extension is left as it is.

### Who can do what

- **Administrators** install, configure, enable, disable, reorder, restart, test and uninstall extensions, write local extensions, read their logs and diagnostics, and pause or resume them all.
- **Members** open **Settings → Extensions** read-only: they see the list, the statuses, the permissions each copy has, and who paused the extensions and since when. The page tells them that only an administrator can change it. Logs and diagnostics stay with administrators, because they can contain message text.
- **Viewer** and **kiosk** accounts have no Extensions section.

Every change an administrator makes is written to the audit log (**Settings → Users & Access → Audit log**) with who made it and which copy it touched: install, enable, disable, settings changed, uninstall, local code saved, pause and resume. The audit log keeps the names of the changed fields, never their values: no secret and no code. Being on the same network gives no right to install anything. Only an administrator account can, and the audit log shows who did.

### Pause all extensions

**Pause all extensions** (top of the page, administrators only, after a confirmation) is the switch for an incident: an extension misbehaves and you want everything quiet now, before finding which one.

- Every running copy stops and none starts while the pause lasts. Messages and events arriving meanwhile are not delivered to extensions, and **Test with a message** is refused.
- Nothing is lost: each copy keeps its on/off switch, its settings and its data. You can still install, configure or turn copies on; they start when you resume.
- A banner says since when and by whom the extensions are paused, and copies show the **Paused** status. **Resume** starts again exactly the copies that are switched on.
- The pause survives a restart of Meshloom.
- If Meshloom cannot read the pause switch when it starts, it plays safe: extensions stay paused, without a date or a name in the banner, until an administrator chooses **Resume**.

It differs from the other two ways to stop extensions:

| | Who | What stops | How to undo it |
|---|---|---|---|
| The switch of one copy | An administrator, in the interface | That copy only | Turn it back on |
| **Pause all extensions** | An administrator, in the interface | Every copy; switches and settings are kept | **Resume** |
| Host lock (`MESHCORE_DISABLE_PLUGINS=true`, see [Environment](/en/docs/deep/environment/)) | Whoever runs the server | The whole plugin system: nothing can be installed, changed or started; you can still view and delete copies, and pause all extensions. Copies restored from a backup come back turned off and stay so | Remove the variable and restart |

When the host lock is set, it wins: the page shows the extensions as turned off by the server, whatever the pause says.

### Local extensions

**New local extension** (in the Installed tab) creates a small Python program kept in Meshloom, from a starting code (an echo that answers `!pling` with `Plong!`, or an empty one). It starts off, with only the permissions you give it. In its drawer:

- **Code**: edit it in the editor and **Save code**. The code is read, never run, when you save: lines worth a look (a module that is not allowed, `print`, a value lost at restart...) are listed with their line number, and a server named in the code (`https://...`) is offered with an **Allow** button that adds it to the servers the extension may contact. A device of your local network named in the code (`http://shelly.local`, `http://192.168.1.20:8080`...) is offered the same way, one at a time, with the line that names it and an **Allow device** button that adds it to the devices the extension may contact. Nothing is looked up or contacted to find them, a password written in the address is never kept, and the machine Meshloom runs on is not offered. A warning shows for ports 8123 (Home Assistant), 5051 (a Meshloom radio proxy) and 8000 (a Meshloom app): an extension allowed there can control that service. Code you did not write (a converted bot, a restored backup, a forum post) may name such devices: only allow a device you recognise. At most 10 devices are offered, the limit of the list.
- **Events and permissions**: the events it listens to and what it may do, changed and saved from the same place.
- **Check** reads the manifest and scans the code without running anything; errors and warnings come with their line.
- **Test with a message** sends a made-up message to the running extension (from a test channel, one of your channels or a contact) and shows what it answered and its log. **Nothing is sent to the radio**: the answers are captured. Save the code first, and turn the extension on to test it.
- The log under the drawer shows what the extension printed, so you can see errors without a command line.

## Bots from 4.18 and earlier

Meshloom 5.0.0 removes the Python bots of **MQTT & Automation**. At its first start, it turns each bot into a **local extension** named after the bot. Your code is kept as it was (you can read and edit it in the extension's **Code** tab), and so are the conversations the bot was limited to and its on/off setting. A small compatibility layer calls your bot function, so it now runs in the same walls as any extension: its own process, permissions, limits, and the pause and the lock above.

- **Review before it starts.** A converted extension never starts by itself, even if the bot was on. It reads *Needs attention* with *Review permissions*, and stays off until an administrator opens it, reads what it may do and chooses **Accept and enable**. The permissions given are those a bot used to have in practice: read every channel, your direct messages and the messages you send, and send replies in the conversation of the message it answers. Untick what the code does not need. The code is also scanned once, and the lines worth a look are listed.
- **Notice.** A banner on every view (administrators only) and **Settings → Extensions** say how many bots were converted and how many still wait for review, until an administrator dismisses the notice in **Settings → Extensions**.
- **Archive.** The original bots are copied to the `fanout_configs_bots_archive` table of the database, then removed from the integrations list. The archive is not part of any backup nor of the support snapshot, and is deleted in a later release (planned for 5.2.0). Creating or changing a bot through the API is refused with `410` and the code `bot_type_retired`.
- **The old lock.** If `MESHCORE_DISABLE_BOTS=true` was set when you upgraded, it is honoured one last time: every converted extension is stored switched off. After that the variable does nothing (Meshloom logs a warning at start if it is still set). To keep all extensions from running, use `MESHCORE_DISABLE_PLUGINS=true` (the `disable_plugins` option of the Home Assistant add-on; its `disable_bots` option is gone, but a leftover `disable_bots: true` is handed to the migration once, like the variable). The Linux package no longer sets any lock in `meshloom.env`: a bot-free install has nothing running until you add an extension and accept its permissions.
- **Backups.** A backup made before 5.0.0 that holds bots restores them as local extensions, switched off and waiting for review. New backups hold no bot.
- **Stopping them all.** There is no "disable bots until restart" any more: use **Pause all extensions** (above) or the host lock.

## What leaves your instance

Nothing, by itself. Meshloom 5.0.0 has no extension store online: the only extensions you can add are the bundled ones, the ones you write and the bots converted from 4.18, and Meshloom contacts no server to list, fetch or update them. Updates of the bundled extensions arrive with Meshloom itself.

An extension reaches the internet only if you accepted the **Connect to the internet** permission, and then only the servers listed in its consent screen, through Meshloom (see above). What it sends there is its own business, which is why reading private channels or direct messages together with that permission deserves a warning. The backup never contains an extension's secrets or stored data (see [Backup](/en/docs/backup/)). A converted bot keeps its code; if it embedded a token in that code, it is in the backup like any local extension's code, so move it to a setting.

## No way back to 4.18

The conversion of bots changes the database when 5.0.0 starts and is not undone by an older version. 4.18 does not know extensions: if you downgrade, your converted bots are not shown as bots. The originals are kept in a table of the database until 5.2.0, but 4.18 does not read it. Before upgrading, download a **full copy of the database** (**Settings → Database → Download meshcore.db**) and keep it: it is the only way back.
