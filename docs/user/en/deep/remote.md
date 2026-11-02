---
title: Meshloom Remote
description: Open your Meshloom from anywhere through the Meshloom Remote relay: turning it on, allowing devices, what the relay sees, and what each status means.
level: deep
order: 24.2
---

Meshloom Remote opens your instance from anywhere, from any device (phone, tablet, computer), without opening a port on your router and without a TwinRocket account. It is **off by default**: while it is off, Meshloom sends nothing to the relay. Everything happens in **Settings → Remote access**.

## Turning it on

An administrator presses **Turn on Meshloom Remote**. The instance registers its own key with the relay and gets its own address, such as `https://k3xwq2m7ta.meshloom.link`. Nobody can open that address until you allow a device.

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

While Remote is on, Meshloom also tells the relay its version, its kind of install, and whether it runs plugins and with which isolation. While Remote is off, nothing is sent. The relay's own terms and privacy policy say what it keeps.

## Status

The badge under **Status** says where the link stands:

| Badge | Meaning and what to do |
|---|---|
| Off | Remote is off; the instance is only reachable on the local network. |
| Not registered | Remote is on but the relay did not register the instance. Press **Retry**; if it keeps failing, this machine may not reach the relay. |
| Connecting, Connected | The link is coming up, or up: allowed devices open the address shown. |
| Offline | The link dropped. Meshloom reconnects by itself, waiting longer between tries (up to 5 minutes). Check this machine's internet connection. |
| Unknown to the relay | The relay forgot the instance and registering again failed. Turn Remote off and on again; if that is not enough, replace the key. |
| Paused | TwinRocket paused this instance. Meshloom tries again by itself after 15 to 20 minutes. |
| Replaced | Another install uses this instance's key, often a copy restored from a backup. A key serves one install only: replace the key here, then allow every device again. |
| Banned | TwinRocket banned the instance; Meshloom stops trying. Contact TwinRocket if you think it is a mistake. |
| Update required | The relay no longer accepts this version: update Meshloom, Remote then reconnects. |
| Relay paused | The service is paused for every instance (maintenance or capacity). Meshloom tries again after 5 to 15 minutes. |

The key fingerprint identifies the instance to the relay; it only changes when you replace the key.

## Turning it off, leaving, replacing the key

- **Turn off**: nothing more is sent to the relay and your devices can no longer open the instance from outside. The address and the allowed devices are kept for when you turn it on again.
- **Leave Meshloom Remote**: the relay forgets the instance's address and Remote turns off. The key is kept: turn Remote on again within 30 days to get the same address back.
- **Replace the key**: a new key and a new address; every allowed device and pending request is forgotten, so each device must be allowed again.

A restored backup never brings Remote back: the restored instance starts with Remote off, no address and no device.

To make your own domain lead to the address, see [Your own domain for Meshloom Remote](/en/docs/deep/remote-domain/). To reach Meshloom with your own tools instead, see [Remote access on your own](/en/docs/deep/remote-self-hosted/).
