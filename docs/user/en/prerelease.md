---
title: Try the pre-release
description: Move to a Meshloom release candidate with one command, stay updated, reach the final release, and go back to the stable channel.
level: start
order: 2.9
---

A pre-release (a release candidate, `5.0.0-rc.1` for example) is the next version before it is final. Trying it helps, and it can have bugs that a stable release would not. If this machine is your only way onto the mesh, stay on stable.

Nothing changes unless you ask: a machine on the stable channel never receives a pre-release.

## Move to the pre-release

Paste this into a terminal, on a fresh machine or on one running 4.18:

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app/next)"
```

It is the normal installer, with the same questions. The summary shows the channel before anything starts. When no pre-release is open, the command says so and changes nothing.

Coming from 4.18, read [Upgrading from 4.18](/en/docs/upgrade-4-18/) first. Meshloom makes a copy of your database before it changes anything, and that copy is your way back.

## What this applies to

| Install | Pre-release |
|---|---|
| Linux package (`apt`, `dnf`), 64-bit | Yes |
| Docker | Yes. With the update helper, updates work from **Settings > Updates**. Without it (Docker Desktop, rootless Docker), run the command again to update |
| 32-bit Raspberry Pi and other armhf machines | No package: the service choice stops and says so, and changes nothing. Docker can run a pre-release there |
| Home Assistant add-on | No. The add-on stays on stable releases and receives 5.0.0 when it is final |
| Install from source | No channel: you choose what you check out |

## Stay up to date

**Settings > Updates** shows the channel. On the pre-release channel it offers each newer release candidate, then the final release, and updates as usual.

The channel is chosen on the machine, by the install command. Meshloom reads it and cannot change it, so nobody can move this machine to another channel from the web interface.

## When the final release is out

The final release reaches you as a normal update. You then have the choice:

- **Go back to the stable channel.** Your version does not change, and only stable releases will be offered:

  ```bash
  /bin/bash -c "$(curl -fsSL https://get.meshloom.app)" -- --channel stable
  ```

- **Stay on the pre-release channel.** The next release candidate will be offered as soon as there is one. The channel never changes by itself.

## Go back to stable

While you are on a release candidate, this returns the machine to the current stable release:

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app/next)" -- --channel stable
```

The command shows what it will do and asks first. It installs the stable version again, and puts back the [copy of your database made before the upgrade](/en/docs/deep/install-paths/#the-copy-made-before-an-upgrade). **What you did since that copy (messages heard, settings, accounts) does not come back with it.** The database you were using is kept aside, next to the copy, under a name with `aside` in it.

If a copy is damaged, or something in the data folder is not a plain file, the command stops and changes nothing. If no copy fits the stable version, the command says so. It can then start from an empty database, only if you answer yes; your current database is still kept aside.

**Do not use the regular install command to come back.** Until the final release is out, it is the 4.18 installer, which does not know about pre-releases. With a Linux package it stops with an error about the package and your version does not change, but the source is left on stable. With Docker it starts 4.18 on a database it cannot read correctly, and says it upgraded. In both cases, run the command above: it notices, and puts the copy back.

Docker works the same way: run the command on that machine, and it puts the stable image back.
