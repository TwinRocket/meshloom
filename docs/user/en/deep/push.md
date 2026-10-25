---
title: Push notifications
description: Notifications in the browser even when the tab is closed. HTTPS required.
level: deep
order: 18
---

Web Push can tell your browser about an incoming message while Meshloom's tab is closed. This page covers what is specific to browsers: HTTPS, the contact address (VAPID), subscribing and the list of devices. Everything else, meaning channels, events, the rules table, Do not disturb, mentions, the bell, backups and the other ways to be notified (e-mail, webhook, ntfy, Discord, Telegram, Gotify, Home Assistant, Apprise), is on [Notifications](/en/docs/deep/notifications/).

Web Push is one channel among the others (the built-in **Web Push** channel). Each browser has its own subscription, which belongs to the account that subscribed it, while the rules (what to notify, and for which conversations) are shared by the whole installation and by every channel. It is separate from [fanout](/en/docs/deep/fanout/). There are no pop-ups from the open tab itself: Web Push is the only way Meshloom notifies a browser. Over plain HTTP, ntfy, Gotify or Home Assistant on your network are the answer.

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

A browser's subscription only decides whether that browser receives notifications. The rules are shared: if you switch on a conversation from your phone, it is on for every subscribed browser. What to notify is chosen in [Notifications](/en/docs/deep/notifications/#the-rules-table), the bell and Do not disturb included.

### Whose devices

Each account sees and manages only its own devices: **Your devices** lists the browsers you subscribed, and no other account can rename, test or remove them. A browser shared by two accounts belongs to the last one that subscribed it.

An administrator also sees **Other accounts' devices**: each device's label, its account and the push service it goes through (never its full address, which works as a password for that browser), with a button to remove it, for a lost phone or a former member. Renaming or testing a device is left to its owner.

A device receives notifications only while its account is enabled and may send messages (member or administrator). A viewer or a kiosk cannot subscribe; the devices of a disabled account stay silent until it is enabled again, and deleting an account deletes its devices. Devices registered before accounts owned them (before Meshloom 5.0, or restored from a backup) belong to the first administrator.

## Service worker and cleanup

`sw.js` shows incoming notifications and, when you click one, focuses or opens the right conversation. If a push service answers `403`, `404` or `410` for a subscription, Meshloom deletes it. After that, subscribe the browser again.

## Endpoints

| Method | Endpoint | Effect |
|--------|----------|--------|
| GET | `/api/push/vapid-public-key` | Public key used to subscribe |
| POST | `/api/push/subscribe` | Register or update a subscription |
| GET | `/api/push/subscriptions` | List your subscriptions; `?scope=all` lists every account's (administrators only, the address reduced to the push service) |
| PATCH | `/api/push/subscriptions/{id}` | Change the label or the language of one of your subscriptions |
| DELETE | `/api/push/subscriptions/{id}` | Delete one of your subscriptions (an administrator: any) |
| POST | `/api/push/subscriptions/{id}/test` | Send a test notification to one of your subscriptions |
| GET | `/api/push/preferences` | **Deprecated.** Defaults, exceptions and VAPID subject |
| PATCH | `/api/push/preferences` | **Deprecated.** Change defaults and/or the VAPID subject |
| PUT | `/api/push/preferences/conversations/{key}` | **Deprecated.** Set or clear an exception for a conversation |

A subscription is unique by its address, so registering again updates the existing one and gives it to the account that registers it. Another account's subscription answers `404`, as if it did not exist. The old `/api/push/conversations` routes no longer exist.

The three `preferences` routes are **deprecated**: they answer a `Deprecation` header and will be removed in 5.2.0. Use `/api/notifications` instead; the details, and what the `409 legacy_managed` answer means, are in [Upgrading and downgrading](/en/docs/deep/notifications/#upgrading-and-downgrading) and [Endpoints](/en/docs/deep/notifications/#endpoints).

## Push services on a private network

Browser push services (Google, Mozilla, Apple) are on the Internet. `POST /api/push/subscribe` refuses an address on a private network (answer `400`, code `egress_private_address`), and Meshloom never sends to one, unless **Accept push endpoints on a private network** is ticked in **Settings → Notifications** (setting `push_allow_private_endpoints` of `/api/settings`). Tick it only for a self-hosted UnifiedPush distributor on your LAN or on this machine. Like the webhook, each push is sent only to the address Meshloom checked (the push service's certificate is still checked against its name), and a redirect is never followed.

The check runs again before every push. A device whose address is malformed, or now points to an address that is refused whatever the settings (cloud metadata such as `169.254.169.254`, documentation ranges), is skipped and removed (when only the DNS answer points there, it is removed once a push an hour or more later still meets it, since a resolver can be wrong once); the server log says `Push endpoint refused for good by the egress guard` with the reason code and the device's id, never its address (it holds a token). A device refused only because the box is unticked, or whose name cannot be found for now, is kept and the log says so once.

On an upgrade, if a device registered before this check is not a browser push service (a UnifiedPush distributor, perhaps on your LAN), the box starts ticked so the device keeps receiving, and a notice in **Settings → Notifications** says so until you dismiss it. Untick the box if none of your devices is on your network. With browser push services only, or only a public distributor such as ntfy.sh, it starts unticked. Only a device whose address is a private IP, a name without a dot or a `.local`, `.lan`, `.home.arpa`, `.internal` or `.localdomain` name counts as being on your network (Meshloom does no DNS lookup for this).

A self-hosted distributor reached through a name that looks public but points to your network (split DNS, a Tailscale `*.ts.net` name) cannot be told apart this way. If the upgrade switches the box off for such a device, **Settings → Notifications** shows a notice with an **Allow private networks** button that ticks the box in one click; nothing is deleted, and a push refused because the box is unticked is never counted as a failure, so the device stays registered. Anyone setting up such a distributor on a fresh install must tick the box by hand.

Restoring a backup taken before this check works the same way: if a restored device is on your network, the box is ticked and the same notice appears.

## When nothing arrives

1. Check that the page is served over HTTPS (or `localhost`).
2. Check the notification permission in your browser and your system.
3. Press **Test** on the device in the list: a failure says only what kind it was (unreachable, timed out, TLS failure, or a subscription the push service no longer accepts), never a host name or an address. The server's log records the kind of error as `Test push failed`.
4. On Apple devices, check the VAPID subject (see above) and look for `403 BadJwtToken` in the server's log.
5. Check that the server can reach the Internet.
6. For the rules (is the event ticked, a mute, Do not disturb, a conversation exception), see [When nothing arrives](/en/docs/deep/notifications/#when-nothing-arrives) on the Notifications page.

At `DEBUG` log level, the answers of the push services are logged. See [Troubleshooting](/en/docs/deep/troubleshooting/).
