---
title: Meshloom Remote
description: "Open your Meshloom from anywhere through the Meshloom Remote relay: turning it on, allowing devices, what the relay sees, and what each status means."
level: deep
order: 24.2
---

Meshloom Remote opens your instance from anywhere, from any device (phone, tablet, computer), without opening a port on your router and without a TwinRocket account. It is **off by default**: while it is off, Meshloom sends nothing to the relay. Everything happens in **Settings → Remote access**.

## Turning it on

An administrator presses **Turn on Meshloom Remote**. The instance registers its own key with the relay, with a proof from Meshloom Community that another station heard your radio recently (see [Proof for Remote and sharing](/en/docs/deep/community/#proof-for-remote-and-sharing)), and gets its own address, such as `https://k3xwq2m7ta.meshloom.link`. Nobody can open that address until you allow a device.

If plugins run without a sandbox on this machine (hooks only), the dialog asks you to confirm first: once the instance is reachable from the internet, anyone who gets into an administrator account could install or configure a plugin that runs code on the machine. Protect every administrator with a passkey or two-factor authentication, and remove the plugins you do not use (**Settings → Extensions**). The **Access security notice** at the top of **Users & Access** repeats this while Remote is on.

## Allowing a device

Each device is allowed once, for your own account:

- **By QR code**, on the local network: press **Show the QR code** and scan it with the device. The code works once and expires after 15 minutes. It gives access to your account: do not share it or post a screenshot of it. Through the relay the QR code is never shown.
- **By access request**, for a device without a camera or away from home: open the instance's address on that device and press **Request access**. It shows a code; type it in **Access requests** on a device you already use. Only approve a device you hold whose screen shows that code, never a code someone sends you, reads to you or asks you to approve.

Then sign in on the device as usual. Through the relay you must **always sign in**: login-free on the local network and trusted networks never apply ([Accounts and access](/en/docs/deep/access/#trusted-networks)). After several failed sign-ins from one network the relay shows a short check page first ([Sign-in delays](/en/docs/deep/access/#sign-in-delays)).

**Allowed devices** lists your devices (an administrator sees every account's). Revoke a lost or unused device; **Revoke every device** cuts them all at once. A browser keeps its access 90 days, an app or script 365 days.

## What the relay sees

The relay is run by TwinRocket. It ends the encrypted connection before passing the traffic on to your instance: it could therefore technically read everything, including your password when you sign in, your messages and your settings. It blocks every device you have not allowed.

So that a password seen by the relay is not enough to get in, use a passkey or turn on two-factor authentication (**Settings → Users & Access**, see [Passkeys](/en/docs/deep/access/#passkeys) and [Two-factor authentication](/en/docs/deep/access/#two-factor-authentication)).

While Remote is on, Meshloom also tells the relay its version, its kind of install, and whether it runs plugins and with which isolation. While Remote is off, nothing is sent. There is no relay account. For each instance the relay keeps:

- its registration: the key, the address, the Meshloom version, the kind of install, whether plugins run and with which isolation;
- the state of the allowed devices (not their names);
- usage figures per instance and month: requests, traffic, how long the instance was connected, errors. They hold no content, and the relay uses them to apply the monthly limit below and to keep its own costs in check;
- a record of the actions the operator takes on an instance (a pause, a ban, a key withdrawn), with the date;
- the [alert e-mail](#alert-e-mail) address, if you give one.

An access request lives 10 minutes and a failed sign-in is remembered for 15 minutes, each under a scrambled tag of the network address, never the address itself. The relay's logs hold no message, address, web address or key; the usage figures are kept for a few months. **Leave Meshloom Remote** deletes the registration and the device state from the relay; the key and the address stay reserved for you for 30 days (see below), and the usage figures expire on their own.

## Status

The badge under **Status** says where the link stands:

| Badge | Meaning and what to do |
|---|---|
| Off | Remote is off; the instance is only reachable on the local network. |
| Not registered | Remote is on but the relay did not register the instance. Press **Retry**; if it keeps failing, this machine may not reach the relay. When Community could not give its proof, the reason shows under the badge: for **No other station has heard this radio lately**, send a flood advertisement, then retry a few minutes later. |
| Connecting, Connected | The link is coming up, or up: allowed devices open the address shown. |
| Offline | The link dropped. Meshloom reconnects by itself, waiting longer between tries (up to 5 minutes). Check this machine's internet connection. |
| Unknown to the relay | The relay forgot the instance and registering again failed. Turn Remote off and on again; if that is not enough, replace the key. |
| Monthly quota reached | The instance went over its [monthly quota](#limits). Remote comes back on the 1st of the next month (UTC): Meshloom tries again by itself every 15 to 20 minutes. The local network is not affected: Meshloom keeps working at home. |
| Paused | TwinRocket paused the instance. Meshloom tries again by itself after 15 to 20 minutes. The local network is not affected. |
| Replaced | Another install uses this instance's key, often a copy restored from a backup. A key serves one install only: replace the key here, then allow every device again. |
| Banned | TwinRocket banned the instance; Meshloom stops trying. Contact TwinRocket if you think it is a mistake. |
| Update required | The relay no longer accepts this version: update Meshloom, Remote then reconnects. |
| Relay paused | The service is paused for every instance (maintenance or capacity). Meshloom tries again after 5 to 15 minutes. |

The key fingerprint identifies the instance to the relay; it only changes when you replace the key.

## Limits

| Limit | Value |
|---|---|
| Traffic per instance and month | 5 GB, counted both ways (requests, responses and live connections) |
| Requests per instance and month | 500,000 (a browser's live connection counts as one) |
| Reset | On the 1st of each month, UTC |
| Past the limit | The instance is paused until the next month: every request is refused, whoever asks. Meshloom shows **Monthly quota reached**, and a confirmed [alert e-mail](#alert-e-mail) gets a warning at 80 % and a notice at the pause. The local network keeps working |
| Requests per instance | 1,200 per minute; beyond that the relay answers "too many requests" for a moment |
| Failed sign-ins | After a few from one network, a check page first (see above) |
| Access request | Valid 10 minutes; at most 5 waiting at once for an instance |
| QR code | One use, 15 minutes |
| Allowed device | 90 days for a browser, 365 days for an app or script |
| Address after **Leave** | Kept 30 days for the same key |

The limits are the same for every instance. Ordinary use of the interface stays far below them; a script that polls every few seconds, or large media, is what reaches them.

## Alert e-mail

An administrator can give an address in **Settings → Remote access → Alert e-mail**. It gets at most one warning at 80 % of the monthly quota and one notice when the instance is paused over it, each month.

- **Confirming**: the relay sends a mail with a link to confirm and a link to stop. Nothing else is sent until the address is confirmed; the link is valid 24 hours.
- **Changing or removing**: type another address to replace it (it must be confirmed again), press **Remove the address**, or follow the stop link in any mail. An address can be set at most 3 times a day.
- **What is kept**: the address stays on the relay and is used for these mails only. Meshloom does not keep it and the relay never shows it back, so the page only says whether one is waiting for confirmation or confirmed. **Leave Meshloom Remote** and replacing the key delete it.

Without a confirmed address the pause happens all the same, without a mail.

## Turning it off, leaving, replacing the key

- **Turn off**: nothing more is sent to the relay and your devices can no longer open the instance from outside. The address and the allowed devices are kept for when you turn it on again.
- **Leave Meshloom Remote**: the relay deletes the instance's registration and Remote turns off. The key stays on this machine and the address stays reserved for 30 days: turn Remote on again within that time to get the same address back, after it a new one is given.
- **Replace the key**: a new key and a new address; every allowed device and pending request is forgotten, so each device must be allowed again.

A restored backup never brings Remote back: the restored instance starts with Remote off, no address and no device.

To make your own domain lead to the address, see [Your own domain for Meshloom Remote](/en/docs/deep/remote-domain/). To reach Meshloom with your own tools instead, see [Remote access on your own](/en/docs/deep/remote-self-hosted/).
