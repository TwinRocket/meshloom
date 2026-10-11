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

Set it in **Settings → Notifications**, in the **VAPID subject** field. It must be `mailto:you@domain.tld` (recommended) or `https://your-host` with no path. When the field is empty, Meshloom uses the environment variable `MESHCORE_VAPID_SUBJECT`, whose default is `mailto:noreply@meshcore.local`.

**Apple requires a real address.** APNs rejects a subject that is not a genuine contact and the `.local` default, answering `403 BadJwtToken`. Set a real address in the interface, or as a fallback in the environment:

```text
MESHCORE_VAPID_SUBJECT=mailto:you@example.com
```

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

## When nothing arrives

1. Check that the page is served over HTTPS (or `localhost`).
2. Check the notification permission in your browser and your system.
3. Send a test from **Settings → Notifications**.
4. Check the defaults and exceptions, also visible at `/api/push/preferences`. A private-key channel stays silent unless an exception turns it on. A muted channel stays silent whatever the settings.
5. On Apple devices, check the VAPID subject (see above) and look for `403 BadJwtToken` in the server's log.
6. Check that the server can reach the Internet.

At `DEBUG` log level, the answers of the push services are logged. See [Troubleshooting](/en/docs/deep/troubleshooting/).
