---
title: Fanout
description: MQTT, bots, webhooks, Apprise, SQS — whatever Meshloom hears can leave again.
level: deep
order: 16
---

Fanout redistributes mesh events to other systems: private MQTT, community MQTT, bots, webhooks, Apprise, SQS, and map uploads. Configurations live in `fanout_configs`. [Meshloom Community](/en/docs/deep/community/) is a separate join state, not a Fanout row.

| Method | Endpoint | Effect |
|---------|----------|-------|
| GET | `/api/fanout` | List configurations |
| POST | `/api/fanout` | Create one |
| PATCH | `/api/fanout/{id}` | Update and reload the module |
| DELETE | `/api/fanout/{id}` | Delete and stop the module |

Disabled configurations are drafts. `GET /api/health` reports `connected`, `disconnected`, or `error`.

## Module types

| Type | What it does |
|------|---------------|
| `mqtt_private` | Publishes to your MQTT broker |
| `mqtt_community` | Publishes raw packets to a community broker |
| `bot` | Runs Python in response to messages |
| `webhook` | HTTP POST / PUT / PATCH with optional HMAC-SHA256 |
| `apprise` | Sends through Apprise URLs |
| `sqs` | Sends JSON envelopes to Amazon SQS |
| `map_upload` | Sends heard repeater and room adverts to map.meshcore.io |

Home Assistant MQTT Discovery has its own page: [Home Assistant](/en/docs/deep/home-assistant/).

## Scope

```json
{"messages": "all", "raw_packets": "all"}
{"messages": "none", "raw_packets": "all"}
{"messages": {"channels": ["key1"], "contacts": "all"}, "raw_packets": "none"}
```

Scope filters decoded messages and raw packets. Contacts, repeater telemetry, and radio health go to every module; each module filters internally. Community MQTT and `map_upload` are locked to raw packets only. Historical decryption never replays fanout events.

Modules may receive messages (`PRIV` or `CHAN`, conversation key, text, sender, ACK state, paths, timestamps), raw packets (`id`, `observation_id`, hex payload, timestamp, optional `decrypted_info`), contacts, telemetry, and 60-second radio health snapshots. For raw packets, `id` is storage identity and may repeat; `observation_id` is unique per RF arrival.

## Bots

Bots run Python entered in the UI through `exec()` with full `__builtins__`. Anyone who can reach the UI can execute arbitrary code on the host. See [Security](/en/docs/deep/security/) and [A trusted network](/en/docs/trust/).

```python
def bot(sender_name, sender_key, message_text, is_dm,
        channel_key, channel_name, sender_timestamp, path):
    if "!echo" in message_text.lower():
        return f"[ECHO] {message_text}"
    return None
```

Optional keyword arguments include `region`, `scoped`, `is_outgoing`, `path_bytes_per_hop`, and `packet_hash`. A bot may return `None`, a string, a list of strings, or `{"region": ..., "message": ...}`. Region scope applies only to channel replies. Bot execution has thread, timeout, concurrency, and rate limits.

`MESHCORE_DISABLE_BOTS=true` disables the system at startup and returns `403` for bot configuration changes. `POST /api/fanout/bots/disable-until-restart` stops bot modules until process restart.

Bots can be created or changed only from a local address or Home Assistant ingress, not through a domain name. See [Security](/en/docs/deep/security/).

## Community MQTT

It publishes raw packets only. A direct packet’s optional `path` is a comma-separated list of hop identifiers, with widths of 1, 2, or 3 bytes according to `path_hash_mode`; it is not a byte-by-byte rendering.

## Webhooks

`hmac_secret` adds an HMAC-SHA256 signature of the JSON body. The header is configured by `hmac_header`, defaulting to `X-Webhook-Signature`, and the value is `sha256=<hex>`.
