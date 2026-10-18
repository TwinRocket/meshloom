---
title: A trusted network
description: Accounts and roles, Python bots, and why Meshloom still belongs on a network you know.
level: start
order: 6
---

Meshloom assumes it runs on a network whose users you know. This is not an oversight, it is the design. It helps to understand what that means.

## Accounts and roles

From Meshloom 5.0, nothing is shown before you sign in. The first time, Meshloom asks you to create the administrator account; after that every visitor signs in with a user account, and the server checks the account's role on every request (see [Recover access](/en/docs/deep/access/#roles)). The administrator can do everything. A member can read and send messages and edit contacts and channels. A viewer reads. A kiosk is read-only for a wall screen and never sees direct messages. Under Home Assistant, Home Assistant signs you in.

Accounts do not turn Meshloom into a service you can leave open to strangers. An administrator can read the whole history of direct messages and channels, write as your node, change the radio's settings and Wi-Fi network, reboot the radio, empty the database and, below, make the machine run code. A member can write as your node. Treat every account as a key to the installation, and give accounts only to people you trust.

Other web sites open in the same browser cannot read Meshloom's answers: the server only talks to its own address, plus any site you list in **Settings > Local Configuration > Allowed origins** (empty by default). Nor can they open the live feed, and Meshloom refuses changes that come from another site (see [Security](/en/docs/deep/security/#what-does-not-exist)).

## Bots run code

Meshloom can run bots: small programs that react to the messages you receive. They are written in Python, and Meshloom runs them as they are, with no sandbox and no list of allowed instructions.

The consequence is direct: **any administrator can make the host machine run any code.** Not only inside the application, but on the machine, with the server's permissions. It is an intentional automation feature, and it is also the most sensitive part of an installation.

While bots are enabled and no account protects the instance (from 5.0 every page needs a signed-in account, so you normally never see it), Meshloom opens a warning dialog ("Unprotected bot execution is enabled"). You can disable bots until the next restart from that dialog, or tick the acknowledgement and dismiss it for this browser. It is not decoration.

Two protections exist:

- Bots are **on by default**, except in the Linux package and the Raspberry Pi image, which turn them off (`MESHCORE_DISABLE_BOTS=true` in `/etc/meshloom/meshloom.env`). The installer asks nothing about this. Docker installs and the Home Assistant add-on keep bots on (the add-on has a `disable_bots` option).
- Setting `MESHCORE_DISABLE_BOTS=true` turns the whole bot system off at startup. No bot runs, changes to bots are refused, and the interface shows the feature as disabled.

If people you do not all know can reach the instance, keep bots disabled.

## The password and HTTPS

Your password protects the interface only as far as the network between you and Meshloom does. Over plain HTTP, the user name and password travel unencrypted on every sign-in. [HTTPS](/en/docs/deep/https/) explains how to set up a certificate, even a self-signed one.

When you update from 4.x, the shared Basic login (`MESHCORE_BASIC_AUTH_USERNAME` and `MESHCORE_BASIC_AUTH_PASSWORD`) becomes the administrator account at the first start, then Meshloom stops reading those variables (remove them). Scripts that still send it keep working until 5.2. An instance that had no password asks you to create the administrator; if its database existed before the update, it also asks for an installation code printed in the server log, so that nobody else on the network can claim it first. The Home Assistant add-on asks for nothing through the sidebar: Home Assistant signs you in, and the first Home Assistant user to open Meshloom becomes its administrator. Its port 8000 is not published by default; to reach Meshloom outside Home Assistant, and set up an account there, give `8000/tcp` a host port in the add-on's **Network** panel.

Under Home Assistant ingress, Meshloom trusts the identity Home Assistant's Supervisor gives it, as every add-on does: Home Assistant and its add-ons trust each other, so an add-on you would not trust with your Home Assistant should not run next to Meshloom either.

## In practice

A few rules prevent most problems:

- **Do not expose port `8000` to the Internet.** Do not forward it on your router. For remote access, use a VPN into your home or office network.
- On a shared network (shared house, office, guest network), give accounts only to people you trust, and keep bots disabled.
- Remember that the radio's private key is given to the server so it can decrypt direct messages. It is kept in memory only, but an administrator can read it back, through the API and in the full backup. A compromised machine is a compromised machine.
- The optional short-link service (**Settings > Share: links and files**) is off by default and never contacted until an administrator turns it on. It uses a key of its own, never the radio's, and backups carry neither that key nor the consent: a restore never turns sharing on and always gives a new key (see [Short links](/en/docs/deep/share/)).
- If you turn on **Settings > Proxy** to share your radio with other apps, know that it has no login at all, and that Basic auth does not cover it.
- Treat channels for what they are. The key is the only access: giving it to someone lets them read and write there.

The details of the security settings, self-signed certificates and related variables are in [Security](/en/docs/deep/security/).
