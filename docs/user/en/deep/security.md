---
title: Security
description: What Meshloom does not protect, and the few locks that exist: Basic auth, turning bots off, an encrypted backup.
level: deep
order: 20
---

Meshloom is designed for a network whose users you know. Several design choices only make sense under that assumption. These choices are deliberate, not oversights.

## What does not exist

**No user accounts.** There are no logins, sessions, roles or per-feature permissions. Anyone who can reach the server can read the history, send messages as your node, change radio settings and edit bots.

**No restriction on where requests come from.** The server accepts requests from any web page (`allow_origins=["*"]`, with `allow_credentials=True`). This makes it easy to open the interface from any device, but it also means a web page open in the same browser can talk to the API.

**Bots run arbitrary Python.** A bot is a small Python program written in the interface. Meshloom runs it as-is, with full access to the language (`exec()` with the complete `__builtins__`). Anyone who can reach Meshloom can therefore run code on the host machine. This is intentional.

None of this weakens MeshCore's own encryption over the air: direct messages and channel messages stay encrypted as usual. These points describe who can use the application, not what travels over the air. The node's private key is the exception on the server side: anyone who reaches the interface, any web page open in a browser that can reach Meshloom included, can read it (see [The node's private key](#the-nodes-private-key)).

## The warning dialog

While bots are enabled and no Basic auth is set, Meshloom opens a full-screen warning titled "Unprotected bot execution is enabled". It offers two ways out:

- **Disable Bots Until Server Restart** turns the bot system off immediately. It comes back at the next restart unless you turn it off for good (see below).
- Tick the acknowledgement box, then **Do Not Warn Me On This Device Again**. The choice is remembered by this browser only, so another browser or phone sees the warning again.

## HTTP Basic authentication

```text
MESHCORE_BASIC_AUTH_USERNAME=...
MESHCORE_BASIC_AUTH_PASSWORD=...
```

Set both, or neither: the server refuses to start with only one. Basic auth protects the pages, the API and the live connection (WebSocket). It is a single shared login with no roles. Without HTTPS the password travels in clear text, so pair it with [HTTPS](/en/docs/deep/https/).

## Disable bots

`MESHCORE_DISABLE_BOTS=true` switches the bot system off at startup. No bot runs, the server answers `403` to any attempt to change bots, and the interface shows the feature as unavailable. The Linux package already sets this in `/etc/meshloom/meshloom.env`.

The **Disable Bots Until Server Restart** button of the warning dialog does the same temporarily, without touching the environment. See [Fanout](/en/docs/deep/fanout/) for bots and the other outputs.

## The node's private key

Meshloom reads the radio's private key each time it connects to the radio, if the radio's firmware allows it, and keeps it in the server's memory only. Meshloom never writes it to its database. If the firmware refuses, Meshloom works without it, but it can no longer decrypt direct messages on the server, and a backup then holds no identity.

The key can always be read back by anyone who reaches the interface: `GET /api/radio/private-key` returns it as hexadecimal text, and both the configuration export in **Settings > Radio** and the full backup in **Settings > Database** contain it (see [Back up your radio](/en/docs/backup/)). There is no switch to turn this off. Because the server accepts requests from any origin, **a web page opened in a browser on a machine that can reach Meshloom can read the key and download the full backup**, Wi-Fi password included. Keep Meshloom on a network you trust, and treat the backup file like the key itself: encrypt it with a passphrase when you keep it anywhere else than on that network.

Importing a key (**Set Private Key**, write-only) never shows the key. A key that does not match the radio the database is bound to is refused until you confirm the identity change (see [First launch](/en/docs/first-run/)).

## Radio proxy

**Settings > Proxy** can make Meshloom behave like a radio on the network, so a phone app or a second Meshloom can use your radio through it. It is off by default. The protocol it speaks has no authentication, and Basic auth does not cover it. Enable it only on a network you trust.

## Wi-Fi password

Meshloom never stores the radio's Wi-Fi password, in its database or in its logs, at any log level. To know whether credentials are stored, it reads the password back from the radio over the companion link, then forgets it: after each connection, at each retry of that check, and on a manual **Retry** while no check has completed. Storing credentials sends the password to the radio and reads it back twice (once before, once to verify). Over a TCP transport the companion link is not encrypted: the password crosses your local network in clear each time Meshloom reads it back or stores it, and anyone who can watch that network can read it. Over USB or Bluetooth it does not cross your network. See [Radio, contacts, and channels](/en/docs/deep/radio/#wi-fi).

## Support snapshot

**Settings > About > Open debug support snapshot** (`/api/debug`) is meant to be pasted into a bug report. Outside the logs it holds versions, settings and counters, never the private key; the keys of contacts and channels appear only as one-way fingerprints. The logs never contain the private key or a Wi-Fi password, at any log level, `DEBUG` included. The logs can contain channel names and channel keys at any level; at `DEBUG` they can also contain repeater and room login passwords and the Bluetooth PIN. Stop copying at the line `STOP COPYING HERE` to leave the logs out.

## What leaves your machine

What each output sends depends on the scope you give it in **Settings > MQTT & Automation**.

- **Community Sharing** only ever carries raw radio packets to a community MQTT server, never readable conversation text.
- **Private MQTT**, **Webhook**, **Apprise** and **Amazon SQS** can carry the full text of messages, according to the channels and contacts you select.
- **Map Upload** sends the adverts of repeaters and room servers, with their position, to a map service (map.meshcore.io or one you choose).
- **Home Assistant** publishes your devices to the MQTT server (broker) you configure.

Some features need Internet access: [push notifications](/en/docs/deep/push/), and [Meshloom Community](/en/docs/deep/community/) when joined (raw-packet publishing, directory, sharing of hashtag channel names). Even with both off, Meshloom checks GitHub for new releases once every 6 hours (with Community on: every five minutes, with the Community mirror as a fallback). It keeps working if that check fails. When you leave Community, Meshloom sends it one last request to record the opt-out and delete your radio's position. After that, it is reached again only if Community surely did not receive that request and the status of your node could not be deleted from the broker at that moment (a single reconnect deletes it, a few tries at most within 24 hours, then nothing), or when you press **Retry** because Community did not confirm the opt-out (see [Meshloom Community](/en/docs/deep/community/#leaving)). Alert e-mails carry the Meshloom logo inside them, so opening one loads nothing from the Internet.

## A reasonable posture

- Keep Meshloom on a network where you know the users.
- Never expose it directly to the Internet. For remote access, use a VPN or a tunnel that adds its own login.
- Set `MESHCORE_DISABLE_BOTS=true` if you do not need bots.
- Encrypt a full backup with a passphrase when you store it away from that network.
- Always put Basic auth behind HTTPS.

The short version is in [A trusted network](/en/docs/trust/).
