---
title: Security
description: What Meshloom does not protect, and the few locks that exist: Basic auth, turning bots off, exporting a key.
level: deep
order: 20
---

Meshloom is designed for a network whose users you know. Several design choices only make sense under that assumption. These choices are deliberate, not oversights.

## What does not exist

**No user accounts.** There are no logins, sessions, roles or per-feature permissions. Anyone who can reach the server can read the history, send messages as your node, change radio settings and edit bots.

**No restriction on where requests come from.** The server accepts requests from any web page (`allow_origins=["*"]`, with `allow_credentials=True`). This makes it easy to open the interface from any device, but it also means a web page open in the same browser can talk to the API.

**Bots run arbitrary Python.** A bot is a small Python program written in the interface. Meshloom runs it as-is, with full access to the language (`exec()` with the complete `__builtins__`). Anyone who can reach Meshloom can therefore run code on the host machine. This is intentional.

None of this weakens MeshCore's own encryption: direct messages, channel keys and the node's private key are protected as usual. These points describe who can use the application, not what travels over the air.

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

The radio's private key is read once at startup and kept in the server's memory only. Meshloom never writes it to disk.

Reading it back through the API is disabled by default:

```text
MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT=false
```

When set to `true`, `GET /api/radio/private-key` returns the key as hexadecimal text, and the configuration export in **Settings > Radio** can include it. Turn this on only on a trusted network, only for a backup or a move to another machine, then turn it off again. Importing a key (**Set Private Key**, write-only) is always available and never shows the key. A key that does not match the radio the database is bound to is refused until you confirm the identity change (see [First launch](/en/docs/first-run/)).

## Radio proxy

**Settings > Proxy** can make Meshloom behave like a radio on the network, so a phone app or a second Meshloom can use your radio through it. It is off by default. The protocol it speaks has no authentication, and Basic auth does not cover it. Enable it only on a network you trust.

## Support snapshot

**Settings > About > Open debug support snapshot** (`/api/debug`) is meant to be pasted into a bug report. Outside the logs it holds versions, settings and counters, never the private key; the keys of contacts and channels appear only as one-way fingerprints. The recent logs can contain channel names or keys, but never the private key. Stop copying at the line `STOP COPYING HERE` to leave the logs out.

## What leaves your machine

What each output sends depends on the scope you give it in **Settings > MQTT & Automation**.

- **Community MQTT** only ever carries raw radio packets, never readable conversation text.
- **Private MQTT, webhooks, Apprise and SQS** can carry the full text of messages, according to the channels and contacts you select.
- **Map upload** sends the position of repeaters and room servers to a map service (map.meshcore.io or one you choose).
- **Home Assistant MQTT** publishes your devices to the broker you configure.

Some features need Internet access: [push notifications](/en/docs/deep/push/), and [Meshloom Community](/en/docs/deep/community/) when joined (raw-packet publishing, directory, sharing of hashtag channel names). Even with both off, Meshloom checks for new releases every five minutes (GitHub first, then the Community mirror). It keeps working if that check fails.

## A reasonable posture

- Keep Meshloom on a network where you know the users.
- Never expose it directly to the Internet. For remote access, use a VPN or a tunnel that adds its own login.
- Set `MESHCORE_DISABLE_BOTS=true` if you do not need bots.
- Keep private-key export disabled except during a backup.
- Always put Basic auth behind HTTPS.

The short version is in [A trusted network](/en/docs/trust/).
