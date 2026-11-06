---
title: Meshloom Remote
description: "Open your Meshloom from anywhere through the Meshloom Remote relay: turning it on, allowing devices, what the relay sees, and what each status means."
level: deep
order: 24.2
---

Meshloom Remote opens your instance from anywhere, from any device (phone, tablet, computer), without opening a port on your router and without a TwinRocket account. It is **off by default**: while it is off, Meshloom sends nothing to the relay. Everything happens in **Settings → Remote access**.

## Turning it on

An administrator presses **Turn on Meshloom Remote**. The instance registers its own key with the relay, with a proof from Meshloom Community that another station heard your radio recently (see [Proof for Remote and sharing](/en/docs/deep/community/#proof-for-remote-and-sharing)), and gets its own address, such as `https://k3xwq2m7ta.meshloom.link`. Nobody can open that address until you allow a device.

If plugins run without a sandbox on this machine (hooks only), the dialog asks you to confirm first: once the instance is reachable from the internet, anyone who gets into an administrator account could install or configure a plugin that runs code on the machine. Protect every administrator with two-factor authentication, and remove the plugins you do not use (**Settings → Extensions**). The **Access security notice** at the top of **Users & Access** repeats this while Remote is on.

## Allowing a device

Each device is allowed once, for your own account:

- **By QR code**, on the local network: press **Show the QR code** and scan it with the device. The code works once and expires after 15 minutes. It gives access to your account: do not share it or post a screenshot of it. Through the relay the QR code is never shown.
- **By access request**, for a device without a camera or away from home: open the instance's address on that device and press **Request access**. It shows a code; type it in **Access requests** on a device you already use. Only approve a device you hold whose screen shows that code, never a code someone sends you, reads to you or asks you to approve.

Then sign in on the device as usual. Through the relay you must **always sign in**: login-free on the local network and trusted networks never apply ([Accounts and access](/en/docs/deep/access/#trusted-networks)). After several failed sign-ins from one network the relay shows a short check page first ([Sign-in delays](/en/docs/deep/access/#sign-in-delays)).

**Allowed devices** lists your devices (an administrator sees every account's). Revoke a lost or unused device; **Revoke every device** cuts them all at once. A browser keeps its access as long as you sign in and use it at least once every 90 days; after that it must be allowed again. An app or script keeps its access 365 days.

A device belongs to the account that allowed it. Deleting an account cuts all its devices, those it approved for someone else included: that person asks for access again.

## What the relay sees

The relay is run by TwinRocket. It ends the encrypted connection before passing the traffic on to your instance: it could therefore technically read everything, including your password when you sign in, your messages and your settings. It blocks every device you have not allowed.

Only two-factor authentication stops a password seen by the relay from being enough to get in (**Settings → Users & Access**, see [Two-factor authentication](/en/docs/deep/access/#two-factor-authentication)). A [passkey](/en/docs/deep/access/#passkeys) avoids typing the password, but the password stays valid.

While Remote is on, Meshloom also tells the relay its version, its kind of install, and whether it runs plugins and with which isolation. While Remote is off, nothing is sent. There is no relay account. For each instance the relay keeps:

- its registration: the key, the address, the Meshloom version, the kind of install, whether plugins run and with which isolation;
- the state of the allowed devices (not their names);
- usage figures per instance and month: requests, traffic, how long the instance was connected, errors. They hold no content, and the relay uses them to apply the monthly limit below and to keep its own costs in check;
- a record of the actions the operator takes on an instance (a pause, a ban, a key withdrawn), with the date;
- the [alert e-mail](#alert-e-mail) address, if you give one.

An access request lives 10 minutes and a failed sign-in is remembered for 15 minutes, each under a scrambled tag of the network address, never the address itself. The relay's logs hold no message, address, web address or key; the usage figures are kept for a few months. **Leave Meshloom Remote** deletes the registration and the device state from the relay; the key and the address stay reserved for you for 30 days (see below), and the usage figures expire on their own.

## What Remote isolates, and what it does not

Every instance's address ends in `meshloom.link`, and browsers treat all these addresses as one site. The relay makes up for most of that.

Isolated:

- **Access to your instance.** A page on another instance can link to yours, nothing more: anything else it tries from the browser is refused.
- **Cookies.** Your session and your device's access use cookies tied to the exact address. A page on another instance can still leave cookies for the whole domain in your browser, but the relay passes on only the ones tied to the address, so those never reach your instance. The relay also removes from an instance's responses any cookie meant for the others.
- **Passkeys.** A passkey created through Remote works only on that address. After **Replace the key**, the address changes: create a new one.

Not isolated:

- **Password managers.** Some treat all of `meshloom.link` as one site, so they can offer your password on another instance's page. Check the address before filling it in and, if your manager allows it, make the saved entry match the exact address. Only two-factor authentication stops a password alone from being enough; a passkey avoids typing it, but it stays valid.
- **The browser's room for cookies.** A page on another instance can fill the room your browser gives to `meshloom.link`. At worst you are signed out, the browser loses its access, or pages refuse to load because the request is too large. It gives that page no access to your instance.

To reset the cookies, delete those of `meshloom.link` and its subdomains in your browser's settings, then allow the device again and revoke the old entry in **Allowed devices**. This applies to every Meshloom Remote instance opened in that browser. For one instance only, the page shown to a browser without access has a **Forget this device** button. It only removes that browser's access to the instance, not your Meshloom session.

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
| Replaced | Another install uses this instance's key, often a [copy of the data folder](#backups-and-the-remote-key). A key serves one install only: replace the key here, then allow every device again. |
| Banned | TwinRocket banned the instance; Meshloom stops trying. [Write to TwinRocket](#help-and-reports) if you think it is a mistake. |
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
| One upload | 100 MiB; beyond that the relay answers "request too large" |
| Requests per instance | 1,200 per minute; beyond that the relay answers "too many requests" for a moment |
| Failed sign-ins | After a few from one network, a check page first (see above) |
| Access request | Valid 10 minutes; at most 5 waiting at once for an instance |
| QR code | One use, 15 minutes |
| Allowed device | 90 days after its last use for a browser, 365 days for an app or script |
| Address after **Leave** | Kept 30 days for the same key |

These are the limits of the free plan, the same for every instance. Ordinary use of the interface stays far below them; a script that polls every few seconds, or large media, is what reaches them.

## Alert e-mail

An administrator can give an address in **Settings → Remote access → Alert e-mail**. It gets at most one warning at 80 % of the monthly quota and one notice when the instance is paused over it, each month.

- **Confirming**: the relay sends a mail with a link to confirm and a link to stop. Nothing else is sent until the address is confirmed; the link is valid 24 hours.
- **Changing or removing**: type another address to replace it (it must be confirmed again), press **Remove the address**, or follow the stop link in any mail. An address can be set at most 3 times a day.
- **What is kept**: the address stays on the relay and is used for these mails only. Meshloom does not keep it and the relay never shows it back, so the page only says whether one is waiting for confirmation or confirmed. **Leave Meshloom Remote** deletes it. Replacing the key deletes it too as long as Remote and Community are on and the relay accepts it, so not if TwinRocket froze or banned the instance. Otherwise it stays on the relay, and only a still-valid stop link from a mail you received can remove it (24 hours in the confirmation mail, 30 days in an alert mail). With Remote or Community off, turn them on and remove the address before replacing the key.

Without a confirmed address the pause happens all the same, without a mail. No mail is sent when an instance goes offline.

## Turning it off, leaving, replacing the key

- **Turn off**: nothing more is sent to the relay and your devices can no longer open the instance from outside. The address and the allowed devices are kept for when you turn it on again.
- **Leave Meshloom Remote** (administrators): the relay deletes the instance's registration and Remote turns off. The key stays on this machine and the address stays reserved for 30 days: turn Remote on again within that time to get the same address back, after it a new one is given. Remote and Community must be on, since Meshloom has to reach the relay. The relay refuses it while the instance is paused (monthly quota included), frozen or banned: turn Remote off instead, and leave once a pause is over.
- **Replace the key**: a new key and a new address; every allowed device and pending request is forgotten, so each device must be allowed again.

To make your own domain lead to the address, set a URL redirect on your side to `https://<name>.meshloom.link`: a real `CNAME` cannot work (error 1014). See [Your own domain for Meshloom Remote](/en/docs/deep/remote-domain/). To reach Meshloom with your own tools instead, see [Remote access on your own](/en/docs/deep/remote-self-hosted/).

## Backups and the Remote key

The Remote key is kept in the instance's database.

- **Meshloom's own backups** (the full backup, **Download meshcore.db**) leave it out. After restoring one, the instance has a new key and Remote is off, with no address and no device: turn it on and allow each device again.
- **Any other copy of the data folder** (a Home Assistant backup, a volume or disk backup, a file copied by hand) holds the key, so keep it as private as the instance. Restoring it brings back the same key and address. If two copies run at once, they push each other off the relay and Meshloom shows **Replaced**. If such a copy may have got out, press **Replace the key**.
- **Restore from the local network.** Restoring a backup normally turns Remote off, which ends the connection you would be using. A full backup is limited to 64 MiB and the relay refuses any upload above 100 MiB. A database copy, which can be larger, goes back on the machine itself ([Database and updates](/en/docs/deep/install-paths/#database-and-updates)). Downloading one through the relay counts towards the monthly traffic.

## On the Home Assistant add-on

Remote works the same way on the add-on. The link goes out from the add-on, so `8000/tcp` can stay unpublished. What differs:

- **Signing in.** A Home Assistant account has no password, no second factor and no passkey, so it cannot sign in through the relay. Create a Meshloom account with a password in **Settings → Users & Access**, and sign in with it on your remote devices. You can allow a device from the sidebar, then sign in on it with that account.
- **Extensions.** Remote makes Meshloom reachable from outside Home Assistant. If you do not use extensions, turn on `disable_plugins` in the add-on's configuration.
- **Updates.** For **Update required**, update from Home Assistant's add-on page.
- **Backups.** A Home Assistant backup holds the database, and so the key (see above).

## Help and reports

- **A question, a problem, or a pause you think is a mistake:** write to [contact@meshloom.app](mailto:contact@meshloom.app).
- **A ban you think is a mistake:** write to [ban@meshloom.app](mailto:ban@meshloom.app).
- **An address that hosts illegal content, phishing or malware:** write to [abuse@meshloom.app](mailto:abuse@meshloom.app), also given on [my.meshloom.app/abuse](https://my.meshloom.app/abuse).
- **Is the service running?** See [my.meshloom.app/status](https://my.meshloom.app/status).
