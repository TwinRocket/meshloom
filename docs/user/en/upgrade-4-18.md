---
title: Upgrading from 4.18
description: What changes when Meshloom 4.x becomes 5.0, the copy of your database made before the migration, why there is no way back in place, and how to go back from the copy.
level: start
order: 2.8
---

Meshloom 5.0 changes the structure of your database when it first starts. An older version does not read the result, so **an installation cannot be moved back to 4.18 in place**. Meshloom protects you with a copy made before anything changes; this page says where it is, what changes, and how to go back from the copy if you need to.

## Before you upgrade

1. **Turn off automatic updates** in **Settings → Updates** until you are ready, so the upgrade happens when you choose.
2. **Download a copy of the database on 4.18, before the upgrade** (**Settings → Database → Download meshcore.db**) and keep it somewhere that is not this machine. It is your own safeguard, on top of the automatic copy below. A copy downloaded on 5.0 is already migrated and scrubbed: it cannot take you back. Also keep the `secrets.key` file of the data folder with it (see [Other install paths](/en/docs/deep/install-paths/#database-and-updates)).
3. **Make a full backup** (**Settings → Database → Backup & Restore**, see [Back up your radio](/en/docs/backup/)). A backup made on 5.0 is a newer format that 4.18 does not fully read: use it to restore on 5.0 or later, never on 4.18.
4. **Note your Python bots** if you use any: they become extensions (below).

## The copy made automatically

The first time 5.0 starts on a 4.18 database, Meshloom makes a copy of the database **before** it changes anything, in the data folder next to the database (see [Other install paths](/en/docs/deep/install-paths/#database-and-updates) for where that is). The copy is a copy of the whole database: it holds your messages, contacts and settings as 4.18 left them. To use the copy you need the `secrets.key` of the data folder from before the upgrade: it stays where it is, so do not delete it. Keep the copy until you are sure you stay on 5.0; Meshloom does not use it again.

## What changes

- **Accounts replace the shared password.** The Basic login of 4.x is gone and its variables are ignored. The first start asks you to create the administrator account, and, because your installation already held data, to type the **installation code** that Meshloom prints in its log (`journalctl -u meshloom`, `docker logs <container>`, or the add-on's **Log** tab; `meshloom-admin setup-code` prints a new one). See [First launch](/en/docs/first-run/) and [Accounts and access](/en/docs/deep/access/). Scripts and Home Assistant sensors that sent the Basic password now use an API token (`Authorization: Bearer`).
- **Login-free access stays off.** Trusted network rules you had are kept but disabled until an administrator turns on login-free access on the local network.
- **Python bots become extensions.** Each bot is turned into a local extension with its code kept, and stays **off** until an administrator reviews its permissions and chooses **Accept and enable**. See [Bots from 4.18](/en/docs/deep/plugins/#bots-from-418-and-earlier).
- **The full backup has a new format.** 4.18 silently ignores what it does not know in it.
- **Meshloom Community stays as it was** on an existing installation. It is off by default on a new one.
- **Source installs need Python 3.13 or later.** Packages, Docker and the add-on bring their own.

## Going back to 4.18

There is no in-place return: 4.18 does not read what 5.0 wrote: the bots converted in 5.0 would not appear as bots, and the notification channels added in 5.0 would not be seen.

To go back, from the copy:

1. Stop Meshloom.
2. Install 4.18 again. With a Linux package, install that version of the package; with Docker, put the 4.18 image back in the Compose file. The update helper never moves to an older version by itself.
3. In the data folder, put the copy of the database made before the migration (or the one you downloaded on 4.18 before the upgrade, never one downloaded on 5.0) in place of `meshcore.db`, with the `secrets.key` from before the upgrade.
4. Start Meshloom.

Everything you did on 5.0 after the upgrade (messages heard, settings, accounts) is lost with it: the copy dates from the moment of the upgrade.
