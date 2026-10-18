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

Set it in **Settings → Notifications**, in the **VAPID subject** field. It must be `mailto:you@domain.tld` (recommended) or `https://your-host` with no path. When the field is empty, Meshloom uses `mailto:noreply@meshcore.local`.

**Apple requires a real address.** APNs rejects a subject that is not a genuine contact and the `.local` default, answering `403 BadJwtToken`. Set a real address in that field.

The environment variable `MESHCORE_VAPID_SUBJECT` is no longer read. If it was still set when you updated, Meshloom copied it into this field once, at the first start, provided the field was empty. The Home Assistant add-on's `vapid_subject` option works the same way: it is copied once into this field, and is deprecated (it goes away in the next release).

See [Apple's documentation](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers). Other push services may accept the default, so the problem often only shows up with the first Apple device.

## Subscribing a browser

In **Settings → Notifications**, choose **Subscribe This Browser**. The list of registered devices then shows each browser, with a **Test** button and a way to unsubscribe.

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

Messages from a blocked contact or name, and messages on a muted channel, never go to Apprise.

### The bell, for one conversation

In the header of a conversation, the bell opens a menu with four tick boxes: **Push**, **Email**, **Webhook** and **Apprise**. Ticking one makes an **exception** to the defaults for that conversation only. If your browser is not subscribed yet, ticking **Push** subscribes it. **Email**, **Webhook** and **Apprise** are greyed out until a destination exists; a link takes you to the settings.

Exceptions are listed in **Settings → Notifications → Conversation exceptions**, where you can remove them.

### Muting a channel

On a channel, the mute button silences everything for a time you choose (15 minutes up to 24 hours, or indefinitely). It also hides the unread counter. It overrides defaults and exceptions, and it is not the same control as the bell.

### E-mail and webhook destinations

In **Settings → Notifications → Delivery destinations**, enter an SMTP server (host, port, encryption, user, password, sender and recipient) and/or a webhook address with an optional HMAC secret. Secrets are not shown again after saving. Leave a secret field empty to keep it. The webhook address is a secret too, since it often holds a token: once saved, its field shows only the scheme and host; type a new address to replace it, or press **Clear** to remove it with its HMAC secret. The JSON backup download leaves them out (see [Security](/en/docs/deep/security/#integration-and-notification-secrets)). Each destination has a test button. This webhook is for Meshloom notifications. It is not the message webhook of fanout.

Meshloom refuses a webhook on a private network (your LAN, this machine, `localhost`, carrier-grade NAT, link-local addresses) unless you tick **Allow a destination on a private network (LAN, localhost)** under the address: Home Assistant, Node-RED or a script on your network need it. The check runs when you save the address and again before each send, and Meshloom then connects only to the address it checked, so a DNS answer that changes in between cannot redirect the call. Cloud metadata addresses (`169.254.169.254` and its equivalents) are always refused. If the host name cannot be found, type the IP address instead. A webhook saved before this check existed keeps working: the box starts ticked for it, and a one-time notice says so.

The e-mail is HTML, with a plain-text copy. The Meshloom logo travels inside the message (an inline image), so opening the e-mail loads nothing from the Internet and contains no link.

### Apprise destination

The **Apprise** destination, in the same place, takes one [Apprise URL](https://appriseit.com/services/) per line. A few examples:

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

Each URL is sent on its own: one failing service does not stop the others. A failed send is retried after 5 s and 30 s when the service is unreachable, answers a server error or asks to slow down (its `Retry-After` is honoured), never when it refuses the request (a wrong token, for instance). A URL with several targets (several ntfy topics, Telegram chats or e-mail recipients) is tried only once, so the targets already reached do not get the message twice. To protect your accounts, Meshloom sends at most one Apprise notification every 2 seconds, keeps 20 waiting and replaces the rest with a single "+N events"; new contacts and advertisements notify at most 10 times an hour, then one "+N new contacts". The last failure (its code, the URL line, its scheme and host only) is in `/api/health` under `notification_statuses.apprise`.

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
| GET | `/api/push/preferences` | Defaults, exceptions and VAPID subject |
| PATCH | `/api/push/preferences` | Change defaults and/or the VAPID subject |
| PUT | `/api/push/preferences/conversations/{key}` | Set or clear an exception for a conversation |

A subscription is unique by its address, so registering again updates the existing one. The old `/api/push/conversations` routes no longer exist.

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
