---
title: Push notifications
description: Notifications even when the tab is closed. HTTPS required.
level: deep
order: 18
---

Web Push can tell your browser about an incoming message while Meshloom's tab is closed. It is separate from [fanout](/en/docs/deep/fanout/): each browser has its own subscription, while the rules (what to notify, and for which conversations) are shared by the whole installation.

There are no pop-ups from the open tab itself. Web Push is the only way Meshloom notifies a browser.

The same rules can also send an **e-mail**, call a **webhook** or notify through **Apprise** (ntfy, Telegram, Discord, Home Assistant and many more). Push is on by default. E-mail, webhook and Apprise are off by default, and only work once you have set up a destination. See "Choosing what notifies" below.

## What you need

**HTTPS.** Browsers only allow the background component that receives notifications (the service worker) on a secure page. A certificate you made yourself can work; see [HTTPS](/en/docs/deep/https/). To reach Meshloom from outside your home with a name and a certificate you control, see [Remote access on your own](/en/docs/deep/remote-self-hosted/).

**Internet access from the server.** Notifications go through services run by browser makers: Google (FCM), Mozilla, or Apple (APNs).

## The contact address (VAPID)

Meshloom signs each notification with a key pair created on first start. The signature also carries a contact address, called the VAPID subject.

Set it in **Settings → Notifications → Channels**, in the **VAPID subject** field of the Web Push card (**Devices and Web Push settings**). It must be `mailto:you@domain.tld` (recommended) or `https://your-host` with no path. When the field is empty, Meshloom uses `mailto:noreply@meshcore.local`.

**Apple requires a real address.** APNs rejects a subject that is not a genuine contact and the `.local` default, answering `403 BadJwtToken`. Set a real address in that field.

The environment variable `MESHCORE_VAPID_SUBJECT` is no longer read. If it was still set when you updated, Meshloom copied it into this field once, at the first start, provided the field was empty. The Home Assistant add-on's `vapid_subject` option works the same way: it is copied once into this field, and is deprecated (it goes away in the next release).

See [Apple's documentation](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers). Other push services may accept the default, so the problem often only shows up with the first Apple device.

## Subscribing a browser

In **Settings → Notifications → Channels**, open **Devices and Web Push settings** on the Web Push card and choose **Subscribe This Browser**. The list of registered devices then shows each browser, with a **Test** button and a way to unsubscribe.

A browser's subscription only decides whether that browser receives notifications. The rules are shared: if you switch on a conversation from your phone, it is on for every subscribed browser.

## Choosing what notifies

### Events

**Settings → Notifications → Default notifications** has one row per event, with four columns: **Push**, **Email**, **Webhook** and **Apprise**. Push is on for every event at the start.

| Event | When it fires |
|-------|---------------|
| New contacts | A companion (a person's radio) is seen for the first time |
| Direct messages | A direct message arrives, including posts from room servers |
| Repeater advertisements | A repeater is seen for the first time |
| Companion advertisements | A companion is seen for the first time |
| Sensor advertisements | A sensor is seen for the first time |
| Found channels | Meshloom finds a new hashtag channel |
| Telemetry alerts | A tracked repeater or contact crosses an alert threshold. The thresholds are set on the **Alerts** page |
| Meshloom updates | A new Meshloom version is available |
| Radio offline | The radio has been unreachable for longer than the delay set below the table (60 s by default). Sent as urgent (the e-mail subject starts with `[URGENT]`), once per outage |
| Radio back online | The radio is reachable again after a **Radio offline** notification, with how long it was down |
| Meshloom started | Meshloom has just started: its version, whether the radio connected and how fast. On by default for push |

**Radio offline** and **Radio back online** never fire for a reboot Meshloom started itself (clock fix, Wi-Fi change) nor while the connection is paused in **Settings → Radio**; pausing ends an outage without a notification. Nothing is sent before the radio has connected once since Meshloom started, nor during the same delay right after a start, so an update or a restart does not raise a false alarm. Set the delay in **Settings → Notifications → Radio offline delay** (`radio_offline_grace_s` in `/api/settings`; 0 notifies at once, up to 3600 s). The radio is checked about every 5 seconds, and a reconnect attempt in progress can delay the check, so the notification may arrive a little after the delay. If Meshloom restarts during an outage, the downtime still counts from the real start.

**Radio offline cannot warn you when Meshloom itself is down** (power cut, host reboot): nothing is running to send it. **Meshloom started** makes an unexpected restart visible. To be warned while it is stopped, use the Home Assistant `binary_sensor.<radio>_connected` with a “for” delay (see `README_HA.md`); a periodic heartbeat is planned.

For a companion, **New contacts** or **Companion advertisements** is enough.

"First seen" alerts only fire when a **new contact** is added, after the radio has finished its start-up. A node you already know, an unknown node and a room server never trigger them.

### Conversations

For channel messages, without an exception:

- public channels and `#` (hashtag) channels notify by push;
- channels with a private key do not.

Direct messages follow the **Direct messages** row. E-mail, webhook and Apprise notifications for a channel only exist if you switch them on for that channel (the bell, below).

Messages from a blocked contact or name, and messages on a muted channel (until the mute ends), never notify on any channel: Web Push, e-mail, webhook and Apprise alike. Only an event marked **Always deliver** still goes through. Nothing changes until you block someone or mute a channel.

### The bell, for one conversation

In the header of a conversation, the bell opens a menu with four tick boxes: **Push**, **Email**, **Webhook** and **Apprise**. Ticking one makes an **exception** to the defaults for that conversation only. If your browser is not subscribed yet, ticking **Push** subscribes it. **Email**, **Webhook** and **Apprise** are greyed out until a destination exists; a link takes you to the settings.

Exceptions are listed in **Settings → Notifications → Conversation exceptions**, where you can remove them.

### Muting a channel

On a channel, the mute button silences everything for a time you choose (15 minutes up to 24 hours, or indefinitely). It also hides the unread counter. It overrides defaults and exceptions, and it is not the same control as the bell.

### Channels

**Settings → Notifications → Channels** (administrators) lists every place Meshloom sends notifications to as a card: its type, a status dot (working, error, paused, off), the last error, an on/off switch, **Test**, **Edit** and **Delete**. The **Web Push** card is always there and cannot be deleted; open **Devices and Web Push settings** on it for this browser's subscription, the other devices, the VAPID subject and the private-endpoint switch. Over plain HTTP the card says Web Push is unavailable.

**Add a channel** opens a dialog in three steps. First pick a type or a preset (Email, Webhook, Apprise, and the services added to the list). Then fill its form, which the server describes itself: required fields are starred, the server's validation errors appear under the field, and a secret (a password, a token, a webhook address) is never shown, not even after saving: the form says a value is set and offers **Replace** and **Clear** (leaving it alone keeps it). A third-party service (a "cloud" one) shows a privacy note and lets you choose whether the message text is included (public channels only by default). **Send a test and continue** sends a test before anything is saved; if it fails, the error is explained and you can fix the form, or continue without a successful test. Last, **What to receive here?** pre-fills the events: **Like Push** (the events Web Push receives, the default), **Alerts only** (radio offline or back, telemetry alerts, mentions, channel changes) or **Everything**. Change it later in the table below.

The `legacy:email`, `legacy:webhook` and `legacy:apprise` channels (your 4.18.x destinations) still follow the older setting until Meshloom delivers from the new rules. Their card's **Edit** opens the same form as in 4.18.x (it saves as you leave a field), and **Remove this destination** empties it; their type is not offered again in **Add a channel**.

### ntfy channels

**Add a channel → ntfy** publishes to an [ntfy](https://ntfy.sh) topic: free, self-hostable, with Android, iOS and desktop apps, and it works for a Meshloom served over plain HTTP on your LAN. Subscribe to the same topic in the ntfy app.

- **Server**: `https://ntfy.sh` by default, or your own (`http://192.168.1.20:8080`). A server on your private network, or on this machine, needs **Allow a destination on a private network**.
- **Topic** (required, write-only): 1 to 64 letters, digits, `-` or `_`. On ntfy.sh the topic is the only secret: anyone who knows it can read and post, so "topics are public by obscurity". The form can generate a random one of 24 URL-safe characters. Meshloom publishes the message as JSON to the server address: the topic is never part of a URL or a log line, and it is never shown again after saving.
- **Access token**, or **user name and password** (write-only), for a server with access control. A token wins when both are set. Changing the server asks you to enter the secrets again, so they never follow a new destination.
- **Priorities**: ordinary events use the default priority (3), urgent ones (*radio offline*, telemetry alerts) the urgent priority (5, which rings on most phones). **Tags** are ntfy tags (emoji short codes show as icons).
- **Address of this Meshloom** (`https://meshloom.example.net/`): a tap on the notification opens the conversation or the node.
- **Markdown** formats the message in the ntfy web app.
- **Certificate check**: it can only be turned off for a server whose every address is on a private network (a self-signed ntfy on your LAN); never for a public host.

Errors a test or the channel card shows: *credentials refused* (401/403), *quota reached* (ntfy.sh's daily allowance is spent: self-host ntfy or subscribe; Meshloom does not retry), *too many requests* (ntfy's `Retry-After` is honoured), *certificate error*, *service error* (a server error or a message that is too long).

ntfy.sh is a third party, and it keeps messages for 12 hours by default. A ntfy.sh channel therefore sends the text of Public and `#` channels only (title only for direct messages and private channels) until you choose **Always**, and has a default limit of 10 messages a minute, 30 an hour and **200 a day**, under the free plan's 250 a day (check [ntfy.sh/pricing](https://ntfy.sh/pricing)), so that a busy channel cannot spend the allowance before noon. A self-hosted server has no daily limit by default.

### E-mail and webhook destinations

In **Settings → Notifications → Channels**, choose **Add a channel** and enter an SMTP server (host, port, encryption, user, password, sender and recipient) and/or a webhook address with an optional HMAC secret. Secrets are not shown again after saving. Leave a secret field empty to keep it. The webhook address is a secret too, since it often holds a token: once saved, its field shows only the scheme and host; type a new address to replace it, or press **Clear** to remove it with its HMAC secret. The JSON backup download leaves them out (see [Security](/en/docs/deep/security/#integration-and-notification-secrets)). Each destination has a test button. This webhook is for Meshloom notifications. It is not the message webhook of fanout.

Meshloom refuses a webhook on a private network (your LAN, this machine, `localhost`, carrier-grade NAT, link-local addresses) unless you tick **Allow a destination on a private network (LAN, localhost)** under the address: Home Assistant, Node-RED or a script on your network need it. The check runs when you save the address and again before each send, and Meshloom then connects only to the address it checked, so a DNS answer that changes in between cannot redirect the call. Cloud metadata addresses (`169.254.169.254` and its equivalents) are always refused. If the host name cannot be found, type the IP address instead. A webhook saved before this check existed keeps working: the box starts ticked for it, and a one-time notice says so.

**Several recipients and a language (e-mail).** An e-mail destination takes several recipients, one per line (commas work too), and an optional Cc (each Cc address gets its own message, once). Meshloom hands them to the mail server in one go: if the server refuses one recipient the whole notification counts as failed, even though the others may have received it, and it is not sent again. The message is written in the language of the channel (Automatic follows the notification language). When the text of a message is withheld (the setting that keeps message text out of third-party services), the subject reads "New message" and the body shows the sender and the conversation but not the text.

**Webhook headers.** Each webhook call carries `X-Webhook-Event`, `X-Webhook-Id` and `X-Webhook-Timestamp` (epoch seconds of that attempt), and `X-Webhook-Signature` (`sha256=` and the HMAC-SHA256 of the body) when a secret is set. The id is the same on every retry of one notification: a receiver such as Node-RED can ignore an id it has already seen. The signature covers the body only, so it did not change. The id and the timestamp are not signed: they help a receiver drop an honest retry, not a replayed request (a signed version is planned). Turning certificate checks off (for a self-signed certificate on your network) is accepted only for a destination on a private network; a public address always keeps its check. An e-mail login and password are never sent without encryption (mode **None**) to a mail server outside your private network.

The e-mail is HTML, with a plain-text copy. The Meshloom logo travels inside the message (an inline image), so opening the e-mail loads nothing from the Internet and contains no link.

### Apprise destination

The **Apprise** channel, added the same way, takes one [Apprise URL](https://appriseit.com/getting-started/universal-syntax/) per line. A few examples:

| Service | URL |
|---------|-----|
| ntfy | `ntfys://ntfy.sh/my-meshloom-topic` (or `ntfys://ntfy.example.org/topic` for your own server) |
| Telegram | `tgram://bot_token/chat_id` (the chat id is required) |
| Discord | `discord://webhook_id/webhook_token` |
| Home Assistant | `hassios://homeassistant.local:8123/long_lived_token` |

The URLs hold tokens, so they are a secret: after saving they read `********` and are never shown again; the JSON backup download leaves them out. Change them by typing new ones; empty the field to remove them. Apprise only accepts the services Meshloom has reviewed (the same list as the [Apprise fanout](/en/docs/deep/fanout/)); a URL it refuses is named by its line when you save. This destination carries the system notifications chosen in the table above; it is not the Apprise fanout, which forwards every message of the channels you pick.

- **Message text.** *Public and # channels only* (the default) sends the text of messages on the Public channel and `#` channels, and only a title for direct messages and private channels ("Message from Léa"). *Always* sends every text; *Never* sends titles only.
- **Format.** Plain text, or Markdown. In Markdown the message text is escaped (a message cannot inject a link or a heading), and in both formats room-wide mentions (`@everyone`, `@here`, `@channel`, `@all`, `@room`, `<@…>`, `<!…>`) are neutralised.
- **Allow a destination on a private network (LAN, localhost).** Off by default, as for the webhook: tick it for an ntfy, Gotify or Home Assistant server on your network or on this machine. The check runs at save and again before each send.

Each URL is sent on its own: one failing service does not stop the others. A failed send is retried after 5 s and 30 s when the service is unreachable, answers a server error or asks to slow down (its `Retry-After` is honoured), never when it refuses the request (a wrong token, for instance). A URL with several targets (several ntfy topics, Telegram chats or e-mail recipients) is tried only once, so the targets already reached do not get the message twice. To protect your accounts, Meshloom sends at most one Apprise notification every 2 seconds, keeps 20 waiting and replaces the rest with a single "+N events"; new contacts and advertisements notify at most 10 times an hour, then one "+N new contacts". The last failure (its code, the URL line, its scheme and host only) is in `/api/health` under `notification_statuses["legacy:apprise"]` (see "How notifications are sent" below).

### How notifications are sent

Each destination (push, e-mail, webhook, Apprise) has its own queue and sends one notification at a time, so a slow or failing service never delays the others, nor push. E-mail sends at most one notification every 10 seconds and Apprise one every 2 seconds. When more than 20 notifications wait for one destination, the next ones are replaced by a single "+N events"; urgent ones (radio offline, telemetry alert) are never folded. New contacts and advertisements notify at most 10 times an hour per destination, then one "+N new contacts". This grouping and this cap apply to every destination, push, e-mail and webhook included, not only Apprise. A webhook that is unreachable, times out, answers a server error or asks to slow down is tried again after 5 s and 30 s (its `Retry-After` is honoured, up to 2 minutes), never when it refuses the request. A message notification that still fails is dropped (it would arrive too late); a radio offline or telemetry alert gets one more try 5 minutes later when the service was unreachable, never after it refused the request. Notifications still waiting when Meshloom stops are not sent after the restart.

The state of each destination is in `/api/health`, under `notification_statuses`, keyed by its id (`builtin:push`, `legacy:email`, `legacy:webhook`, `legacy:apprise`): `status` (`connected`, `error`, `paused` or `disconnected`), the last error as a code, never a password or a token (administrators only), and the time of the last successful send. Meshloom keeps a short log of failed, grouped, dropped and cancelled notifications, without their text; successful sends are only counted.

The test button sends a test notification to every URL; a failure names the URL line and the kind of failure, never the token.

### Language

**Settings → Notifications → Notification language** sets the language of e-mail and Apprise notifications: **Automatic**, **Français** or **English**. Automatic follows the browsers subscribed to push: English only when most of them (a strict majority) use English, French on a tie. Push notifications always keep the language of the browser that subscribed, and the webhook sends data, not text.

### Proof of change

Any change of a delivery destination (e-mail, webhook or Apprise) sends a push notification **Notification destination changed** to every subscribed browser, naming what changed. It cannot be turned off: if you did not make the change, someone else did.

## Behind the scenes

### Service worker and cleanup

`sw.js` shows incoming notifications and, when you click one, focuses or opens the right conversation. If a push service answers `403`, `404` or `410` for a subscription, Meshloom deletes it. After that, subscribe the browser again.

### Endpoints

| Method | Endpoint | Effect |
|--------|----------|--------|
| GET | `/api/push/vapid-public-key` | Public key used to subscribe |
| POST | `/api/push/subscribe` | Register or update a subscription |
| GET | `/api/push/subscriptions` | List subscriptions |
| PATCH | `/api/push/subscriptions/{id}` | Change the label or the language |
| DELETE | `/api/push/subscriptions/{id}` | Delete a subscription |
| POST | `/api/push/subscriptions/{id}/test` | Send a test notification |
| GET | `/api/push/preferences` | **Deprecated.** Defaults, exceptions and VAPID subject |
| PATCH | `/api/push/preferences` | **Deprecated.** Change defaults and/or the VAPID subject |
| PUT | `/api/push/preferences/conversations/{key}` | **Deprecated.** Set or clear an exception for a conversation |

A subscription is unique by its address, so registering again updates the existing one. The old `/api/push/conversations` routes no longer exist.

The three `preferences` routes are **deprecated**: they answer a `Deprecation` header and will be removed in 5.2.0. They keep working as before and every change they make is copied into the notification rules (`GET /api/notifications/rules`), for the four media push, e-mail, webhook and Apprise only; other channels are not visible there. The `Deprecation` header carries the date as RFC 9745 asks (`@1792368000`). Meshloom delivers from the rules: a change made through `PATCH /api/notifications/rules` or `PUT .../rules/conversations/{key}` takes effect at once and is copied back, for push, e-mail, webhook and Apprise, into what these routes and **Settings → Notifications** show (a change you make there is copied into the rules the same way). A change of destination, or a deletion of the `legacy:email`, `legacy:webhook` and `legacy:apprise` channels, still answers `409 legacy_managed`: those destinations are set in Settings. Turning on e-mail, webhook or Apprise for a conversation when that destination is not configured answers `400` with the code `channel_missing`. The same goes for `notification_destinations` in `PATCH /api/settings` and `POST /api/settings/notification-destinations/test`, which now tests the destination like `POST /api/notifications/channels/{id}/test` (codes `invalid_config`, `auth_failed`, `timeout`, `rate_limited`, `service_error`, `egress_*`, at most one test per 5 seconds).

### Notification channels API

Administrators manage the channels under `/api/notifications`:

| Method | Endpoint | Effect |
|--------|----------|--------|
| GET | `/api/notifications/types` | The channel types: JSON Schema (secret fields are `writeOnly`), default limits |
| GET, POST | `/api/notifications/channels` | List and create channels (secrets read `********`) |
| POST | `/api/notifications/channels/test` | Test a configuration before saving it (nothing is stored) |
| GET, PATCH, DELETE | `/api/notifications/channels/{id}` | Read, change (the type cannot change) and delete a channel; `builtin:push` cannot be deleted (`403 builtin_channel`) |
| POST | `/api/notifications/channels/{id}/test` | Send a test; one per 5 seconds per channel, then `429 rate_limited` |
| GET, PATCH | `/api/notifications/rules` | Channels per event, `always_deliver`, the global pause (`paused_until`), the radio offline delay |
| PUT | `/api/notifications/rules/conversations/{key}` | Channels of one conversation (`{"channels": {"id": true}}`, or `null` to clear) |
| GET | `/api/notifications/deliveries` | The delivery journal (failures and suppressions, never the text) and hourly counters |
| GET | `/api/notifications/events` | The event catalogue |

To keep a secret when changing a channel, leave it out, send `null` or send `********`; `""` clears it. Errors are `{"code": ..., "params": ...}` and never contain a host, an address or an exception text. Creating, deleting, enabling or disabling a channel, or changing where one sends, notifies Web Push (this cannot be switched off) and the other enabled channels, and is written to the audit log.

### Push services on a private network

Browser push services (Google, Mozilla, Apple) are on the Internet. `POST /api/push/subscribe` refuses an address on a private network (answer `400`, code `egress_private_address`), and Meshloom never sends to one, unless **Accept push endpoints on a private network** is ticked in **Settings → Notifications** (setting `push_allow_private_endpoints` of `/api/settings`). Tick it only for a self-hosted UnifiedPush distributor on your LAN or on this machine. Like the webhook, each push is sent only to the address Meshloom checked (the push service's certificate is still checked against its name), and a redirect is never followed.

The check runs again before every push. A device whose address is malformed, or now points to an address that is refused whatever the settings (cloud metadata such as `169.254.169.254`, documentation ranges), is skipped and removed (when only the DNS answer points there, it is removed once a push an hour or more later still meets it, since a resolver can be wrong once); the server log says `Push endpoint refused for good by the egress guard` with the reason code and the device's id, never its address (it holds a token). A device refused only because the box is unticked, or whose name cannot be found for now, is kept and the log says so once.

On an upgrade, if a device registered before this check is not a browser push service (a UnifiedPush distributor, perhaps on your LAN), the box starts ticked so the device keeps receiving, and a notice in **Settings → Notifications** says so until you dismiss it. Untick the box if none of your devices is on your network. With browser push services only, or only a public distributor such as ntfy.sh, it starts unticked. Only a device whose address is a private IP, a name without a dot or a `.local`, `.lan`, `.home.arpa`, `.internal` or `.localdomain` name counts as being on your network (Meshloom does no DNS lookup for this).

A self-hosted distributor reached through a name that looks public but points to your network (split DNS, a Tailscale `*.ts.net` name) cannot be told apart this way. If the upgrade switches the box off for such a device, **Settings → Notifications** shows a notice with an **Allow private networks** button that ticks the box in one click; nothing is deleted, and a push refused because the box is unticked is never counted as a failure, so the device stays registered. Anyone setting up such a distributor on a fresh install must tick the box by hand.

Restoring a backup taken before this check works the same way: if a restored device is on your network, the box is ticked and the same notice appears.

## When nothing arrives

1. Check that the page is served over HTTPS (or `localhost`).
2. Check the notification permission in your browser and your system.
3. Send a test from **Settings → Notifications**. A destination on a private network that is not allowed is refused at once, before any connection. Past that check, when a test fails, Meshloom says only what kind of failure it was (unreachable, timed out, TLS failure, the HTTP status the webhook answered, or a subscription the push service no longer accepts); it never shows the network error itself, a host name or an address (so a page cannot use the test to probe your network). The server's log records the kind of error, plus the webhook's host and HTTP status, as `Test push failed` or `Notification test (webhook) failed`; it never records the webhook address or its token.
4. Check the defaults and exceptions, also visible at `/api/push/preferences`. A private-key channel stays silent unless an exception turns it on. A muted channel stays silent whatever the settings.
5. On Apple devices, check the VAPID subject (see above) and look for `403 BadJwtToken` in the server's log.
6. Check that the server can reach the Internet.

At `DEBUG` log level, the answers of the push services are logged. See [Troubleshooting](/en/docs/deep/troubleshooting/).
