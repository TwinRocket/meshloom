---
title: Push notifications
description: Notifications even when the tab is closed. HTTPS required.
level: deep
order: 18
---

Web Push can tell your browser about an incoming message while Meshloom's tab is closed. It is separate from [fanout](/en/docs/deep/fanout/): each browser has its own subscription, while the rules (what to notify, and for which conversations) are shared by the whole installation.

There are no pop-ups from the open tab itself. Web Push is the only way Meshloom notifies a browser.

The same rules can also send an **e-mail** or call a **webhook**. Push is on by default. E-mail and webhook are off by default, and only work once you have set up a destination. See "Choosing what notifies" below.

## What you need

**HTTPS.** Browsers only allow the background component that receives notifications (the service worker) on a secure page. A certificate you made yourself can work; see [HTTPS](/en/docs/deep/https/).

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

**Settings → Notifications → Default notifications** has one row per event, with three columns: **Push**, **Email** and **Webhook**. Push is on for every event at the start.

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

Direct messages follow the **Direct messages** row. E-mail and webhook notifications for a channel only exist if you switch them on for that channel.

### The bell, for one conversation

In the header of a conversation, the bell opens a menu with three tick boxes: **Push**, **Email** and **Webhook**. Ticking one makes an **exception** to the defaults for that conversation only. If your browser is not subscribed yet, ticking **Push** subscribes it. **Email** and **Webhook** are greyed out until a destination exists; a link takes you to the settings.

Exceptions are listed in **Settings → Notifications → Conversation exceptions**, where you can remove them.

### Muting a channel

On a channel, the mute button silences everything for a time you choose (15 minutes up to 24 hours, or indefinitely). It also hides the unread counter. It overrides defaults and exceptions, and it is not the same control as the bell.

### E-mail and webhook destinations

In **Settings → Notifications → Delivery destinations**, enter an SMTP server (host, port, encryption, user, password, sender and recipient) and/or a webhook address with an optional HMAC secret. Secrets are not shown again after saving. Each destination has a test button. This webhook is for Meshloom notifications. It is not the message webhook of fanout.

Meshloom refuses a webhook on a private network (your LAN, this machine, `localhost`, carrier-grade NAT, link-local addresses) unless you tick **Allow a destination on a private network (LAN, localhost)** under the address: Home Assistant, Node-RED or a script on your network need it. The check runs when you save the address and again before each send, and Meshloom then connects only to the address it checked, so a DNS answer that changes in between cannot redirect the call. Cloud metadata addresses (`169.254.169.254` and its equivalents) are always refused. If the host name cannot be found, type the IP address instead. A webhook saved before this check existed keeps working: the box starts ticked for it, and a one-time notice says so.

The e-mail is HTML, with a plain-text copy. The Meshloom logo travels inside the message (an inline image), so opening the e-mail loads nothing from the Internet and contains no link.

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

Browser push services (Google, Mozilla, Apple) are on the Internet. `POST /api/push/subscribe` refuses an address on a private network (answer `400`, code `egress_private_address`), and Meshloom never sends to one, unless **Accept push endpoints on a private network** is ticked in **Settings → Notifications** (setting `push_allow_private_endpoints` of `/api/settings`). Tick it only for a self-hosted UnifiedPush distributor on your LAN or on this machine. Like the webhook, each push is sent only to the address Meshloom checked, and a redirect is never followed.

On an upgrade, if a device registered before this check is not a browser push service (a UnifiedPush distributor, perhaps on your LAN), the box starts ticked so the device keeps receiving, and a notice in **Settings → Notifications** says so until you dismiss it. Untick the box if none of your devices is on your network. With browser push services only, it starts unticked.

## When nothing arrives

1. Check that the page is served over HTTPS (or `localhost`).
2. Check the notification permission in your browser and your system.
3. Send a test from **Settings → Notifications**. A destination on a private network that is not allowed is refused at once, before any connection. Past that check, when a test fails, Meshloom says only what kind of failure it was (unreachable, timed out, TLS failure, the HTTP status the webhook answered, or a subscription the push service no longer accepts); it never shows the network error itself, a host name or an address (so a page cannot use the test to probe your network). The server's log records the kind of error, plus the webhook's host and HTTP status, as `Test push failed` or `Notification test (webhook) failed`; it never records the webhook address or its token.
4. Check the defaults and exceptions, also visible at `/api/push/preferences`. A private-key channel stays silent unless an exception turns it on. A muted channel stays silent whatever the settings.
5. On Apple devices, check the VAPID subject (see above) and look for `403 BadJwtToken` in the server's log.
6. Check that the server can reach the Internet.

At `DEBUG` log level, the answers of the push services are logged. See [Troubleshooting](/en/docs/deep/troubleshooting/).
