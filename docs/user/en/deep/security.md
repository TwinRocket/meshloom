---
title: Security
description: Basic auth, turning bots off, exporting a key. None of this invents accounts.
level: deep
order: 20
---

Meshloom is designed for a trusted network. Several architectural choices depend on that assumption.

## What does not exist

**No user accounts.** There are no sessions, authorization model, or per-feature permissions. Anyone who reaches the UI can read history, send messages, change radio settings, and edit bots.

**No origin restriction.** The backend allows all origins (`allow_origins=["*"]`, with `allow_credentials=True`). This makes LAN access easy, but a third-party page in the same browser can also call the API.

**Arbitrary bot execution.** Bots run user-supplied Python through `exec()` with full `__builtins__`. Anyone on the network can execute arbitrary code on the Meshloom host. This is intentional.

These choices do not weaken MeshCore message encryption, channel keys, or the node private key. They describe application access.

## HTTP Basic authentication

```text
MESHCORE_BASIC_AUTH_USERNAME=...
MESHCORE_BASIC_AUTH_PASSWORD=...
```

Both variables are required together. Basic auth covers HTTP and WebSocket, but it is one shared credential pair with no roles. Credentials are clear text without TLS, so use [HTTPS](/en/docs/deep/https/).

## Disable bots

`MESHCORE_DISABLE_BOTS=true` disables execution at startup, returns `403` for bot configuration changes, and shows an unavailable message. `POST /api/fanout/bots/disable-until-restart` stops bot modules until the process restarts without changing the environment. See [Fanout](/en/docs/deep/fanout/).

## Node private key

Meshloom exports the radio private key into memory only; it never writes it to disk. API export is disabled by default:

```text
MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT=false
```

When enabled, `GET /api/radio/private-key` returns the key as hexadecimal. Enable it only on a trusted network and only for backup or migration. `PUT /api/radio/private-key` import remains available; it does not expose key material.

## Debug snapshot

`/api/debug` is intended for bug reports. Non-log fields expose no keys or channel names beyond bot names. Recent logs may contain channel names or keys, but never the private key. Stop copying at `STOP COPYING HERE` to omit logs.

## What fanout sends out

Community MQTT (a Fanout module you configure) is locked to raw packets and never carries decoded conversation text. Private MQTT, webhooks, Apprise, and SQS can carry full message text according to their scope.

Internet egress is required for [push notifications](/en/docs/deep/push/) and, when joined, [Meshloom Community](/en/docs/deep/community/) (raw-packet publish, directory, hashtag-name share). Even an install that opts out of both still checks for updates on GitHub, once every 6 hours (every five minutes, with the Community release mirror as a fallback, while Community is on); it keeps working without that access.

## A reasonable posture

- Keep Meshloom on a network where you know the users.
- Do not expose it directly to the Internet; use a VPN or tunnel for remote access.
- Set `MESHCORE_DISABLE_BOTS=true` if bots are unnecessary.
- Keep private-key export disabled except during backup.
- Put Basic auth behind HTTPS.

See [A trusted network](/en/docs/trust/) for the short version.
