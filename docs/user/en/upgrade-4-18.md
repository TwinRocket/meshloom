---
title: Upgrading from 4.18
description: What changes when Meshloom 4.x becomes 5.0, the copy of your database made before the migration, why there is no way back in place, and how to go back from that copy.
level: start
order: 2.8
---

Meshloom 5.0 changes the structure of your database when it first starts. An older version does not read the result, so **an installation cannot be moved back to 4.18 in place**. Meshloom protects you with a copy made before anything changes; this page says where it is, what changes, and how to go back from the copy if you need to.

## Before you upgrade

1. **Turn off automatic updates** in **Settings → Updates** until you are ready, so the upgrade happens when you choose.
2. **Download a copy of the database on 4.18, before the upgrade** (**Settings → Database → Download meshcore.db**) and keep it somewhere that is not this machine. It is your own safeguard, on top of the automatic copy below. A copy downloaded on 5.0 is already migrated and scrubbed: it cannot take you back.
3. **Make a full backup** (**Settings → Database → Backup & Restore**, see [Back up your radio](/en/docs/backup/)). A backup made on 5.0 is a newer format that 4.18 does not fully read: use it to restore on 5.0 or later, never on 4.18.
4. **Note your Python bots** if you use any: they become extensions (below).

## The copy made automatically

When 5.0 has to change the structure of your database, it first makes a copy next to it, named after the structure it holds and the version that made it: `meshcore.db.schema87.before-5.0.0.db` (schema 87 is what every 4.18 uses). It sits in the data folder: `/var/lib/meshloom/` for the Linux package, `./data` for Docker, `data/` for a copy of the code, `/config` for the Home Assistant add-on. A 4.18 database has no `secrets.key`, so no key file is copied. The copy takes as much room as the database, and Meshloom does not upgrade if there is not enough: the log says how much it needs. Only you read it (it holds everything the database holds). Keep it until you are sure you stay on 5.0. Details are in [Other install paths](/en/docs/deep/install-paths/#the-copy-made-before-an-upgrade).

## What changes

- **Accounts replace the shared password.** The Basic login of 4.x is gone and its variables are ignored. The first start asks you to create the administrator account, and, because your installation already held data, to type the **installation code** that Meshloom prints in its log (`journalctl -u meshloom`, `docker logs <container>`, or the add-on's **Log** tab; `meshloom-admin setup-code` prints a new one). See [First launch](/en/docs/first-run/) and [Accounts and access](/en/docs/deep/access/). Scripts and Home Assistant sensors that sent the Basic password now use an API token (`Authorization: Bearer`).
- **Login-free access stays off.** Trusted network rules you had are kept but disabled until an administrator turns on login-free access on the local network.
- **Python bots become extensions.** Each bot is turned into a local extension with its code kept, and stays **off** until an administrator reviews its permissions and chooses **Accept and enable**. See [Bots from 4.18](/en/docs/deep/plugins/#bots-from-418-and-earlier).
- **The full backup has a new format.** 4.18 silently ignores what it does not know in it.
- **Meshloom Community stays as it was** on an existing installation. It is off by default on a new one.
- **Source installs need Python 3.13 or later.** Packages, Docker and the add-on bring their own.

## Going back to 4.18

There is no in-place return: 4.18 does not read what 5.0 wrote. The bots converted in 5.0 would not appear as bots, and the notification channels added in 5.0 would not be seen. Everything received or changed since the upgrade stays out of what you go back to. There are two ways:

- **A new instance.** Leave this one as it is and start again from a new install.
- **A fresh 4.18 install with your copy of the data.** On another machine, or on this one after removing Meshloom and moving its data folder away: install 4.18, stop it, put your copy in place of `meshcore.db` in its data folder and remove `meshcore.db-wal`, `meshcore.db-shm` and `meshcore.db.migration-in-progress`, then start it. Choose the copy by its schema number (`schema87` for 4.18), not by the version in its name. The commands for the copy are in [Going back to an older version](/en/docs/deep/install-paths/#going-back-to-an-older-version), which also says what a machine that went through `get.meshloom.app/next` needs afterwards.

Never run the 4.18 install script on a machine running 5.0 or a pre-release. It does not know them, and can start 4.18 on a database it cannot read.

On Home Assistant, restore the backup that Home Assistant offers to make before an update (keep that option ticked when you update): the restore brings back the old database.

A copy that you downloaded on 4.18 before the upgrade works too, if the automatic copy is gone: put it in place of `meshcore.db` the same way. A copy downloaded on 5.0 does not.
