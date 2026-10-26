---
title: A trusted network
description: Accounts and roles, extensions that run code, and why Meshloom still belongs on a network you know.
level: start
order: 6
---

Meshloom assumes it runs on a network whose users you know. This is not an oversight, it is the design. It helps to understand what that means.

## Accounts and roles

From Meshloom 5.0, nothing is shown before you sign in. The first time, Meshloom asks you to create the administrator account; after that every visitor signs in with a user account, and the server checks the account's role on every request (see [Accounts and access](/en/docs/deep/access/#roles)). The administrator can do everything. A member can read and send messages and edit contacts and channels. A viewer reads. A kiosk is read-only for a wall screen and never sees direct messages (see [Kiosk screen](/en/docs/deep/access/#kiosk-screen)). Under Home Assistant, Home Assistant signs you in.

Accounts do not turn Meshloom into a service you can leave open to strangers. An administrator can read the whole history of direct messages and channels, write as your node, change the radio's settings and Wi-Fi network, reboot the radio, empty the database and, below, make the machine run code. A member can write as your node. Treat every account as a key to the installation, and give accounts only to people you trust.

Other web sites open in the same browser cannot read Meshloom's answers: the server only talks to its own address, plus any site you list in **Settings > Local Configuration > Allowed origins** (empty by default). Nor can they open the live feed, and Meshloom refuses changes that come from another site (see [Security](/en/docs/deep/security/#what-accounts-protect-and-what-they-do-not)).

## Extensions run code

Meshloom can run extensions (plugins): small Python programs that react to the messages you receive and may answer them. Each one runs in a process of its own, with only the permissions an administrator accepted when installing it, and, depending on how Meshloom was installed, walls the operating system puts around it (see [Isolation](/en/docs/deep/plugins/#isolation)). That is much stricter than the Python bots of 4.18 and earlier, which ran inside Meshloom with no limit at all; 5.0.0 converted them into extensions that stay off until an administrator reviews them (see [Bots from 4.18](/en/docs/deep/plugins/#bots-from-418-and-earlier)).

It is still code you choose to trust. Where the system offers no walls (isolation `none`, shown in **Settings → Extensions**), a hostile extension could read Meshloom's files, and an administrator can write one. **Any administrator can therefore get code running on the host machine.** It is an intentional automation feature, and it is also a sensitive part of an installation.

Two protections exist:

- Nothing runs by default: an extension has to be added by an administrator, who sees what it may do first. The installer asks nothing about this.
- Setting `MESHCORE_DISABLE_PLUGINS=true` locks the whole extension system at startup. No extension can be installed, changed or started, and the interface shows them as turned off by the server (the add-on has a `disable_plugins` option).

If people you do not all know can reach the instance, lock extensions.

Who can install, configure or run one is decided by the account's role, not by being on your network: only an administrator can, a member sees the list read-only, and viewer and kiosk accounts see nothing. Every change is written to the audit log with who made it, so you can tell afterwards who installed what. When one misbehaves, **Pause all extensions** stops them all at once without losing their settings (see [Plugins](/en/docs/deep/plugins/#pause-all-extensions)).

## The password and HTTPS

Your password protects the interface only as far as the network between you and Meshloom does. Over plain HTTP, the user name and password travel unencrypted on every sign-in. [HTTPS](/en/docs/deep/https/) explains how to set up a certificate, even a self-signed one.

When you update from 4.x, the shared Basic login (`MESHCORE_BASIC_AUTH_USERNAME` and `MESHCORE_BASIC_AUTH_PASSWORD`) becomes the administrator account at the first start, then Meshloom stops reading those variables (remove them). Scripts that still send it keep working until 5.2 ([The 4.x Basic login](/en/docs/deep/access/#the-4x-basic-login)). An instance that had no password asks you to create the administrator; if its database existed before the update, it also asks for an installation code printed in the server log, so that nobody else on the network can claim it first. The Home Assistant add-on asks for nothing through the sidebar: Home Assistant signs you in, and the first Home Assistant user to open Meshloom becomes its administrator. Its port 8000 is not published by default; to reach Meshloom outside Home Assistant, and set up an account there, give `8000/tcp` a host port in the add-on's **Network** panel.

Under Home Assistant ingress, Meshloom trusts the identity Home Assistant's Supervisor gives it, as every add-on does: Home Assistant and its add-ons trust each other, so an add-on you would not trust with your Home Assistant should not run next to Meshloom either.

## In practice

A few rules prevent most problems:

- **Do not expose port `8000` to the Internet.** Do not forward it on your router. For remote access, use a VPN into your home or office network.
- On a shared network (shared house, office, guest network), give accounts only to people you trust, and lock extensions (`MESHCORE_DISABLE_PLUGINS=true`).
- Remember that the radio's private key is given to the server so it can decrypt direct messages. It is kept in memory only, but an administrator can read it back, through the API and in the full backup. A compromised machine is a compromised machine.
- The optional sharing service (short links and photos, **Settings > Share: links and files**) is off by default and never contacted until an administrator accepts its terms. Once on, your Meshloom sends the address you shorten or the photo you share to a service run by the project (like Community, once joined), with this Meshloom's IP address and a pseudonymous key of its own, never the radio's. The service keeps short links for 365 days and files for 30 days. A **public** photo can be seen by anyone who has its link, and photos sent on the Public channel or in a hashtag channel can never be private. An **encrypted** photo (private channels and direct messages only) hides its content from the service and from people who only have the link, but it has no forward secrecy and anyone who holds the conversation's key can read it. Backups carry neither the key nor the consent: a restore never turns sharing on and always gives a new key (see [Short links and photos](/en/docs/deep/share/)).
- The online Store, which finds and updates extensions published on GitHub, has its own switch, off by default and separate from Community: until an administrator turns it on, Meshloom contacts no Store server. Once on, it contacts only `github.com` and GitHub's download servers (and later `store.meshloom.app`): when you press Refresh, every 6 hours while an extension from a repository is installed, and when you add, install or update one (an automatic update downloads inside the update window). They see your IP address and `Meshloom/<version>`, never your radio, contacts, messages or the list of your extensions. Opening the Store tab sends nothing. Extensions from a custom repository are neither reviewed, signed nor revoked online. A backup never carries the switch (see [What leaves your instance](/en/docs/deep/plugins/#what-leaves-your-instance)).
- If you turn on **Settings > Proxy** to share your radio with other apps, know that it has no login at all: accounts do not cover it, and anyone who can reach its port (5051 by default) can use your radio and send as your node. That is a decision, because the apps that speak this protocol cannot sign in. Keep that port inside your network.
- Treat channels for what they are. The key is the only access: giving it to someone lets them read and write there.

The details of the security settings, self-signed certificates and related variables are in [Security](/en/docs/deep/security/).
