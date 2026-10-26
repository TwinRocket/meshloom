---
title: Fanout
description: MQTT, webhooks, Apprise, SQS — whatever Meshloom hears can leave again.
level: deep
order: 16
---

Fanout sends what Meshloom hears to other systems (to be told about events yourself, on your phone or in a chat, use [Notifications](/en/docs/deep/notifications/) instead): your own MQTT broker, Home Assistant, webhooks, chat and e-mail services through Apprise, Amazon SQS, community packet collectors, and the public MeshCore map. In the interface it is called **MQTT & Automation**.

Each destination is an **integration**. To add one:

1. Open **Settings → MQTT & Automation**.
2. Choose **Add Integration** and pick a type.
3. Fill in the form, then choose **Save as Enabled** (or **Save as Disabled** to keep it for later).

The page carries a notice that integrations are an experimental feature in open beta. Each integration shows its state, such as **Connected**, **Disconnected** or **Error**. For an error, **View latest error** shows the last message.

[Meshloom Community](/en/docs/deep/community/) is not an integration. It is a separate membership with its own page.

## Types of integration

| Type | What it does |
|------|--------------|
| Private MQTT | Publishes messages and raw packets to your own MQTT broker |
| Home Assistant MQTT Discovery | Makes your radio, repeaters and contacts appear as devices in Home Assistant. See [Home Assistant](/en/docs/deep/home-assistant/) |
| Community MQTT / meshcoretomqtt | Sends raw packets to a community collector. Presets exist for **MeshRank** and **LetsMesh** (US and EU) |
| Webhook | Sends each message as JSON over HTTP, with an optional signature. Not the notification webhook, see [Webhooks](#webhooks) |
| Apprise | Forwards each message to Discord, Telegram, Slack, e-mail, SMS and a hundred other services. Notifications moved to [channels](/en/docs/deep/notifications/), see [Apprise](#apprise) |
| Amazon SQS | Drops each event into an Amazon SQS queue |
| Map Upload | Sends the repeaters and room servers you hear to map.meshcore.io |

### Private MQTT

You give the address and port of your broker (default port 1883), optional credentials, TLS, and a topic prefix (default `meshcore`). Meshloom publishes to:

- `<prefix>/dm:<contact key>` for direct messages;
- `<prefix>/gm:<channel key>` for channel messages;
- `<prefix>/raw/dm:<key>`, `<prefix>/raw/gm:<key>` or `<prefix>/raw/unrouted` for raw packets.

Messages leave **decrypted**, in plain text, including those you send. Only point it at a broker you trust.

### Community MQTT

This sends raw packets to a collector run by a community, such as LetsMesh or MeshRank, so your radio also acts as an observer. Only raw packets leave, never decrypted messages. The default is the LetsMesh US server, over WebSockets. A region code (IATA) is required, and an e-mail address is optional (it lets the collector link the node to you). With LetsMesh, your radio signs the connection pass with its key; MeshRank needs no pass. Besides the packets, the collector receives a status message every five minutes: radio name, model, firmware version and radio settings.

This is a different thing from the official [Meshloom Community](/en/docs/deep/community/). Both can run at the same time.

### Map Upload

It sends the adverts of repeaters and room servers that share a position to map.meshcore.io. It needs the radio's private key to sign them, so the radio firmware must allow key export. A given node is sent at most once per hour.

**It starts in dry-run mode.** In dry-run, Meshloom only writes what it would send to its log. Nothing reaches the map until you untick **Dry Run (log only, no uploads)** in the form. An optional geofence restricts uploads to nodes within a radius of your own radio.

## Choosing what is sent: the scope

Each integration has a **scope**: which messages it receives, and whether it also receives raw packets.

- **Messages**: all, none, only the channels and contacts you list, or all except the ones you list. With "only", channels and contacts added later are not included automatically.
- **Forward raw packets**: on or off. Only Private MQTT and Amazon SQS give you the choice.

Some types have a fixed scope:

| Type | Scope |
|------|-------|
| Community MQTT, Map Upload | Raw packets only, never messages |
| Webhook, Apprise, Home Assistant | Messages as you choose, never raw packets |

The scope filters only messages and raw packets. Contacts, repeater telemetry and radio health snapshots (every 60 seconds) go to every integration, and each one keeps what it needs.

Decrypting old packets later, after you add a key, never triggers integrations. Adding a key does not replay a week of notifications.

What a message contains: its type (direct or channel), the conversation key, the text, the sender, whether it was acknowledged, the paths it took and the times. A raw packet has two identifiers. `id` identifies the stored packet, and a packet heard again by another route shares it. `observation_id` is unique for each arrival over the air: use it to count. Its numbering starts again when Meshloom restarts.

## Bots

Python bots no longer exist in 5.0.0: they are **extensions** now. Add, write and manage them in **Settings → Extensions** (the bundled **Echo** extension, which answers `!pling` with `Plong!`, is the example to start from). Bots you already had were converted into local extensions that wait for your review before they start. See [Plugins](/en/docs/deep/plugins/) for permissions and limits, and [Bots from 4.18](/en/docs/deep/plugins/#bots-from-418-and-earlier).

### Sharing a link or an image from a script

Extensions (the successors of the bots) have no sharing permission yet and cannot reach Meshloom's own API, so they cannot shorten links or upload images. A script that runs outside Meshloom can, with an [API token](/en/docs/deep/access/#api-and-device-tokens) of the `send` scope, through the same two routes as the interface. Sharing must be on (**Settings > Share: links and files**): the administrator's consent covers scripts too, and the quotas are those of the Meshloom, not of the script. A refused request answers with a `share_*` code (`share_disabled`, `share_quota_exceeded`, `share_rate_limited`, `share_invalid_url`...) described on the [Short links](/en/docs/deep/share/#when-something-goes-wrong) page.

```python
import httpx

api = httpx.Client(
    base_url="http://meshloom.local:8000/api",
    headers={"Authorization": "Bearer mlt_..."},
)

# A short link: JSON {"url": ...}, answers 201 {"id", "url", "expires_at"}.
link = api.post("/share/links", json={"url": "https://example.org/a/very/long/address"})
link.raise_for_status()

# An image: the raw bytes as the body (not multipart), its Content-Type
# (jpeg, png, gif or webp) and X-Share-Mode: public. Answers 201
# {"id", "url", "expires_at", "size", "mode", "text"}; "text" is the composer's
# message, "📷 <url>".
with open("photo.jpg", "rb") as f:
    image = api.post(
        "/share/files",
        content=f.read(),
        headers={"Content-Type": "image/jpeg", "X-Share-Mode": "public"},
    )
image.raise_for_status()

# Neither route sends anything over the radio: post the text yourself.
api.post(
    "/messages/channel",
    json={"channel_key": "0123456789abcdef0123456789abcdef", "text": image.json()["text"]},
)
```

An encrypted image (`X-Share-Mode: encrypted`) also needs `X-Share-Conversation-Type` (`channel` or `contact`) and `X-Share-Conversation-Id` (the channel's `share_conversation_id` from `GET /api/channels`, or the contact's public key); its message starts with `🔒`. Meshloom shows such a message as `[image]`, `[encrypted file]` or `[link]` in push notifications and e-mails, never the address (see [Notifications](/en/docs/deep/notifications/)). The fanout webhook, MQTT and the other machine integrations still carry the message text as it was sent.

## Webhooks

There are two webhooks in Meshloom, and they are not the same. This one, the **fanout webhook**, is an integration for machines: it sends **every message** of the scope you chose, as JSON, to a script, Node-RED or n8n. The **notification webhook** (a *Webhook* channel in [Notifications](/en/docs/deep/notifications/#e-mail-and-webhook-destinations)) sends the **events** you tick in the rules table (a direct message, radio offline, a telemetry alert...), with its own signature and headers (`X-Webhook-Id`, `X-Webhook-Timestamp`). Use the fanout webhook to feed a system with the traffic, the notification one to be told about events.

Meshloom sends each message as JSON, with the method you choose (`POST`, `PUT` or `PATCH`) and the extra headers you set. It waits up to 10 seconds for an answer. Each request carries an `X-Webhook-Event` header.

If you set an **HMAC secret**, Meshloom signs the body with HMAC-SHA256 and sends the result as `sha256=<hex>` in a header. The header is `X-Webhook-Signature` unless you name another.

## Secrets

Secrets are write-only: the Apprise addresses, the webhook URL, header values and HMAC secret, the MQTT passwords and the Amazon SQS access key ID, secret key and session token are never shown again once saved. Their field stays empty with **Unchanged** in it: leave it empty to keep the stored secret, type a new one to replace it, or press **Clear** to remove it when you save (**Keep** cancels). The Apprise box shows the services and hosts of the stored addresses; typing in it replaces them all. The webhook URL field shows the scheme and host of the stored URL, and the integration list shows the same. The webhook headers keep their names, each stored value shown as `********`: leave it to keep it, type a new value to replace it, or remove its line. If you change where a secret goes (the webhook URL, the MQTT broker, its port or TLS, the SQS endpoint), the secret field becomes required: type the secret again, or clear it. For a webhook this covers its header values too. A restore that points an integration somewhere else drops its secret and says so. A restored integration that was allowed to reach a private network comes back disabled, with a notice: check its address before enabling it.

## Destinations on your network

Webhook, Apprise, Amazon SQS (custom endpoint) and Map Upload (custom API address) refuse a destination on your local network, on this machine or on a cloud metadata address, unless you tick **Allow a destination on a private network (LAN, localhost)** in that integration. This way a changed setting cannot turn the server into a probe of your network. Tick it for Home Assistant, Gotify, ntfy, Node-RED, LocalStack or a script on your LAN; cloud metadata addresses stay refused even then.

The address is checked when you save an enabled integration and again before each send (for SQS, when the integration starts). A refused one shows an error such as "this address is on a private network" and nothing is sent. The webhook and Map Upload connect only to the address that was checked and never follow a redirect.

Integrations you had set up before this check keep working: the upgrade ticks the box for them, and a one-time notice at the top of the page lists them. Untick it in an integration that points to a public service. The box ticked by the upgrade covers only the address it was ticked for: when you change the address, it unticks itself, and you tick it again if the new address is on your network.

## Apprise

**Notifications have moved.** To be told about events (direct messages, mentions, radio offline, new contacts, alerts) on Telegram, Discord, ntfy, Home Assistant, Gotify or any Apprise service, add a **channel** in [Notifications](/en/docs/deep/notifications/): it has its own rules, a pause, a test button, retries, a delivery log and a text policy that keeps private messages out of third-party services. The Apprise output described here stays, and keeps doing what it did: forward every message of the channels and contacts you pick, rendered with your own templates. It has no rules, no pause and no per-event choice. A one-click conversion of an Apprise output into a notification channel is planned for a later release; until then, if the same address is in both, you will be notified twice.

Give one address per line. Each message is sent to all of them. You can write your own text templates for direct messages and channel messages, with variables such as the sender, the text, the channel, the number of hops, and the signal strength. Messages you sent yourself are not forwarded unless you tick the option. For Discord, you can keep the webhook's own name and picture.

Apprise skips messages from a blocked contact or name and messages on a muted channel (until the mute ends), like the conversation list. The machine outputs (Private MQTT, Webhook, Amazon SQS) still receive them: a script or a broker decides for itself. To be notified of system events (radio offline, new contacts, alerts) through Apprise, use an Apprise channel in [Notifications](/en/docs/deep/notifications/#apprise-destination) instead.

Meshloom accepts these Apprise services:

- **On a server you choose** (the address goes through the private-network check above): JSON, XML and form webhooks (`json://`, `xml://`, `form://`), ntfy (`ntfy://`; a bare topic goes to ntfy.sh), Gotify, Home Assistant (`hassio://`), Matrix, e-mail (`mailto://`: the SMTP server is the address checked), MQTT, Signal (REST API), Rocket.Chat, Mattermost, Nextcloud and Nextcloud Talk, Emby, and an Apprise API server (`apprise://`). Each also has its `s` form with TLS (`jsons://`, `ntfys://`...).
- **A public service with its own address**: Telegram (`tgram://`), Discord, Pushover (`pover://`), Slack, Pushbullet (`pbul://`), Free Mobile, SFR, Google Chat (`gchat://`), Webex, Pushsafer, Prowl, IFTTT, Join and Opsgenie. Their usual web addresses, such as a Discord webhook link, work too.

Every other Apprise service is refused, private network or not: desktop and local ones (`dbus://`, `windows://`, `syslog://`...), the ones that use cloud credentials (`sns://`, `ses://`), and the ones nobody has reviewed yet. A Telegram address needs a chat id (`tgram://bot_token/chat_id`): without one, Apprise would message whoever last wrote to the bot. Meshloom never follows a redirect for Apprise, and it sends only to the host you typed: Matrix server discovery (`.well-known`) and the e-mail PGP key lookup (`wkd=yes`) are turned off. An address that was already saved and is now refused stays in the list; its integration shows the error until you fix it. Restoring an older backup that holds such an address brings its integration back disabled, with a notice: fix the address before enabling it.

## Amazon SQS

You give the queue URL. The region and endpoint (useful with LocalStack) are optional. Without keys, Meshloom uses the server's normal AWS credentials. Each message is a JSON object with `event_type` (`message` or `raw_packet`) and `data`. Messages are sent decrypted.

## From the API

The interface uses these endpoints. A saved integration is checked every time, enabled or not.

| Method | Endpoint | Effect |
|--------|----------|--------|
| GET | `/api/fanout` | List the integrations |
| POST | `/api/fanout` | Create one |
| PATCH | `/api/fanout/{id}` | Update it and restart it |
| DELETE | `/api/fanout/{id}` | Stop it and delete it |

`GET /api/health` reports the state of each enabled integration in `fanout_statuses`. A disabled integration is simply not started.
