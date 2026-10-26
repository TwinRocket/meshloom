---
title: Notification webhook
description: The webhook body v2 of the notification channels, field by field, with one example per event and how to check its signature in Python and Node-RED.
level: deep
order: 17.5
---

A **Webhook** notification channel (see [Notifications](/en/docs/deep/notifications/#e-mail-and-webhook-destinations)) sends each notification as an HTTP `POST` with a JSON body to an address you control: Home Assistant, Node-RED, n8n or your own script. This page describes the body and the headers so that a receiver can rely on them.

## Body format: v1 or v2

The channel's form has a **Body format** field:

- **v2 (documented, recommended)** is the format described on this page. A webhook channel created from 5.0.0 on starts with it.
- **v1 (legacy)** is the body of 4.18.x, kept byte for byte so that an existing receiver does not break. Every webhook channel that existed before this field keeps v1 until you switch it in its form.

The webhook imported from the 4.18.x settings (`legacy:webhook`) always sends v1, since its settings stay in the 4.18.x form. To receive v2, add a new webhook channel (**Add a channel → Webhook**) and remove the old destination once your receiver is ready.

The JSON Schema of the v2 body is published next to this page: [notifications-webhook.schema.json](https://raw.githubusercontent.com/TwinRocket/meshloom/main/docs/user/en/deep/notifications-webhook.schema.json). It is generated from the code, and a test fails when the two differ.

## The v2 body

```text
{
  "version": 2,              always 2
  "event": "...",            the event, see the examples below
  "event_id": "...",         unique id of this event
  "occurred_at": 1767225600, when it happened (epoch seconds, UTC)
  "dedup_key": "...",        the same on every retry of one notification
  "urgent": false,           true for an urgent event: radio offline, telemetry alert, channel changed
  "instance": {
    "name": "...",           the radio's name
    "radio_public_key": "..." the radio's public key (64 hex characters)
  },
  "data": {...},             typed per event
  "rendered": {
    "title": "...",          the notification as the other channels show it,
    "body": "..."            in the channel's language
  }
}
```

- `data` never holds more than the v1 body of the same event, and less in two places. A message's `text` is `null` when the channel's **Message text** setting holds it back (**Public channels only** for a direct message or a private channel, or **Never**); `rendered.body` then holds no text either. And no message carries its conversation key: for a private channel, that key is the channel's secret. A v1 body sends the text whatever the setting.
- `radio_public_key` is your radio's own public key, the one every node in range already hears in its adverts. It lets one receiver tell several Meshloom instances apart.
- `rendered` is in the channel's **Language of the notifications**.
- New fields may be added to `data` within version 2. A receiver should ignore the fields it does not know. A change that removes or renames a field means a version 3.
- The three `summary.*` events are the summaries the channel's grouping and quiet hours produce. Their `event_id` is derived from their content, so a retry keeps the same id. Their `occurred_at` is the time of each attempt, though: the body of a summary differs from one retry to the next, so deduplicate on `event_id` or `X-Webhook-Id`, never on the body.

## Headers and signature

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |
| `X-Webhook-Version` | `2` |
| `X-Webhook-Event` | the event, as in the body |
| `X-Webhook-Id` | the `dedup_key`: the same on every retry of one notification |
| `X-Webhook-Timestamp` | epoch seconds of this attempt |
| `X-Webhook-Signature` | `sha256=` then the HMAC-SHA256, in hexadecimal, of the timestamp, a dot and the body; only when the channel has an HMAC secret |

The signature covers the timestamp, unlike v1 (which signs the body alone):

```text
X-Webhook-Signature = "sha256=" + hex(HMAC-SHA256(secret, X-Webhook-Timestamp + "." + body))
```

To check a request, compute the same value over the **raw bytes** of the body as received, compare it in constant time, and refuse a timestamp that is too old (five minutes is a good window): a request replayed later then fails. To ignore an honest retry, remember the `X-Webhook-Id` values you have already handled.

### Python

```python
import hashlib
import hmac
import time


def verify(secret: str, timestamp: str, body: bytes, signature: str, max_age: int = 300) -> bool:
    """True when the request is signed with `secret` and less than `max_age` seconds old."""
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        return False
    if age > max_age:
        return False
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return hmac.compare_digest("sha256=" + mac.hexdigest(), signature)
```

### Node-RED

In a **function** node placed after an **http in** node (method `POST`) whose **Do not parse request body** box is ticked. That option exists from Node-RED 4.1 on. The signature covers the exact bytes Meshloom sent, so check it on the raw body and parse the JSON afterwards, as the snippet does: never on `msg.payload` parsed then serialised again, which changes the bytes (a decimal such as a telemetry `value` of `4.0` comes back as `4`) and makes the check fail. On an older Node-RED, which always parses a JSON body, update it, or check the signature in a script or a service in front of it.

```javascript
// Function node after an "http in" node (POST) with "Do not parse request body" ticked,
// so msg.payload is the raw body (a Buffer). Add the `crypto` module in the node's
// Setup tab, and set the secret in the flow's environment variables.
const secret = env.get("MESHLOOM_WEBHOOK_SECRET");
const timestamp = msg.req.get("X-Webhook-Timestamp") || "";
const signature = msg.req.get("X-Webhook-Signature") || "";
if (!Buffer.isBuffer(msg.payload)) {
    node.error("Tick \"Do not parse request body\" on the http in node");
    return null;
}
const raw = msg.payload.toString("utf8");

const age = Math.abs(Date.now() / 1000 - Number(timestamp));
const expected = "sha256=" + crypto.createHmac("sha256", secret)
    .update(timestamp + "." + raw).digest("hex");
const a = Buffer.from(expected);
const b = Buffer.from(signature);
if (!(age <= 300 && a.length === b.length && crypto.timingSafeEqual(a, b))) {
    node.warn("Meshloom webhook: bad signature or stale request");
    return null;
}
msg.payload = JSON.parse(raw);
return msg;
```

## One example per event

These bodies are produced by Meshloom's own code (radio named `Base`, channel in English, **Message text** set to **Always**).

### `message.dm`

```json
{
  "version": 2,
  "event": "message.dm",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "099a0aa364018cda",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "type": "PRIV",
    "text": "On se retrouve au relais ?",
    "sender_name": "Alice",
    "channel_name": "",
    "mention": false
  },
  "rendered": {
    "title": "Message from Alice",
    "body": "On se retrouve au relais ?"
  }
}
```

### `message.channel`

```json
{
  "version": 2,
  "event": "message.channel",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "ae9da36225fbd73b",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "type": "CHAN",
    "text": "Bonjour",
    "sender_name": "Bob",
    "channel_name": "Public",
    "mention": false
  },
  "rendered": {
    "title": "Public",
    "body": "Bonjour"
  }
}
```

### `message.mention`

```json
{
  "version": 2,
  "event": "message.mention",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "22a2711e5e8950bf",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "type": "CHAN",
    "text": "@[Base] tu me reçois ?",
    "sender_name": "Bob",
    "channel_name": "Public",
    "mention": true
  },
  "rendered": {
    "title": "Mention in Public by Bob",
    "body": "@[Base] tu me reçois ?"
  }
}
```

### `contact.first_seen`

```json
{
  "version": 2,
  "event": "contact.first_seen",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "b49e378692f3e372",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "public_key": "ab12cd34ef560123456789abcdef0123456789abcdef0123456789abcdef0123456789ab",
    "name": "Alice",
    "contact_type": 1
  },
  "rendered": {
    "title": "New contact: Alice",
    "body": "Alice appeared on the mesh"
  }
}
```

### `channel.found`

```json
{
  "version": 2,
  "event": "channel.found",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "783d8aaa6df4925a",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "name": "#meshloom",
    "channel_key": "A1B2C3D4E5F60718293A4B5C6D7E8F90"
  },
  "rendered": {
    "title": "New channel detected: #meshloom",
    "body": "New channel detected: #meshloom"
  }
}
```

### `telemetry.alert`

```json
{
  "version": 2,
  "event": "telemetry.alert",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "4b5884b6c2d75a9d",
  "urgent": true,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "rule_id": "battery",
    "public_key": "ab12cd34ef560123456789abcdef0123456789abcdef0123456789abcdef0123456789ab",
    "name": "Relais Nord",
    "metric": "battery",
    "unit": "V",
    "value": 3.46,
    "threshold": 3.5,
    "op": "lt"
  },
  "rendered": {
    "title": "Low battery: Relais Nord",
    "body": "3.46 V (threshold 3.5 V)"
  }
}
```

### `radio.offline`

```json
{
  "version": 2,
  "event": "radio.offline",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "4cf48071f6e14756",
  "urgent": true,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "offline_s": 300,
    "connection": "TCP: 192.168.1.20:5000"
  },
  "rendered": {
    "title": "Radio offline for 5 min (TCP 192.168.1.20:5000)",
    "body": "The radio is unreachable. Meshloom keeps trying to reconnect."
  }
}
```

### `radio.online`

```json
{
  "version": 2,
  "event": "radio.online",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "c76b51d7385fcc87",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "downtime_s": 125,
    "connection": "TCP: 192.168.1.20:5000"
  },
  "rendered": {
    "title": "Radio back online after 2 min",
    "body": "The radio is reachable again (TCP 192.168.1.20:5000)."
  }
}
```

### `app.started`

```json
{
  "version": 2,
  "event": "app.started",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "ccd49217b402106a",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "version": "5.0.0",
    "connect_s": 12,
    "radio_connected": true
  },
  "rendered": {
    "title": "Meshloom started (5.0.0)",
    "body": "Radio connected in 12 s"
  }
}
```

### `app.update`

```json
{
  "version": 2,
  "event": "app.update",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "6bf0631fa24481f7",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "current": "5.0.0",
    "latest": "5.1.0"
  },
  "rendered": {
    "title": "Meshloom update available",
    "body": "5.1.0 is available (you have 5.0.0)"
  }
}
```

### `notify.destination_changed`

```json
{
  "version": 2,
  "event": "notify.destination_changed",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "64e626bdf16a8d9f",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "kinds": [
      "webhook"
    ]
  },
  "rendered": {
    "title": "Notification destination changed",
    "body": "Changed: webhook. If this was not you, check the settings."
  }
}
```

### `notify.channel_changed`

```json
{
  "version": 2,
  "event": "notify.channel_changed",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "cbaa36625dc4d2ab",
  "urgent": true,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "action": "created",
    "channel_type": "webhook",
    "name": "Node-RED"
  },
  "rendered": {
    "title": "Notification channel changed",
    "body": "Channel \"Node-RED\" created. If this was not you, check the settings."
  }
}
```

### `notify.test`

```json
{
  "version": 2,
  "event": "notify.test",
  "event_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
  "occurred_at": 1767225600,
  "dedup_key": "00000000000000000000000000000000",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {},
  "rendered": {
    "title": "Low battery: Test repeater",
    "body": "3.46 V (threshold 3.5 V)"
  }
}
```

### `summary.grouped`

```json
{
  "version": 2,
  "event": "summary.grouped",
  "event_id": "ef2f20bc5ba52d181a4775668e7eddfc",
  "occurred_at": 1767225600,
  "dedup_key": "ef2f20bc5ba52d181a4775668e7eddfc",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "count": 7,
    "dms": 2,
    "private": 0,
    "others": 0,
    "channels": {
      "Public": 5
    }
  },
  "rendered": {
    "title": "+7 messages: 5 in Public, 2 DMs",
    "body": "+7 messages: 5 in Public, 2 DMs"
  }
}
```

### `summary.first_seen`

```json
{
  "version": 2,
  "event": "summary.first_seen",
  "event_id": "f2957bc1a8d16995273116e523f285f6",
  "occurred_at": 1767225600,
  "dedup_key": "f2957bc1a8d16995273116e523f285f6",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "count": 4
  },
  "rendered": {
    "title": "+4 new contacts",
    "body": "+4 new contacts"
  }
}
```

### `summary.digest`

```json
{
  "version": 2,
  "event": "summary.digest",
  "event_id": "83165c6200c16c6d10b8c0f1a1507abd",
  "occurred_at": 1767225600,
  "dedup_key": "83165c6200c16c6d10b8c0f1a1507abd",
  "urgent": false,
  "instance": {
    "name": "Base",
    "radio_public_key": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
  },
  "data": {
    "scope": "quiet_hours",
    "count": 9,
    "conversations": 3,
    "items": [
      [
        "Public",
        6
      ]
    ],
    "more": 0,
    "dms": 3,
    "private": 0,
    "contacts": 2,
    "others": 0
  },
  "rendered": {
    "title": "Quiet hours summary",
    "body": "During quiet hours: 9 messages in 3 conversations (Public 6, 3 DMs), 2 new contacts"
  }
}
```
