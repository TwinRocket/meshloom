---
title: Try the pre-release
description: Install or move to a Meshloom release candidate with one command, and how to go back.
level: start
order: 2.9
---

A pre-release (a release candidate, `5.0.0-rc.1` for example) is the next version before it is final. It can have bugs that a stable release would not. A machine that does not run the command below never receives one.

## Before you start

Copy your data first, or try the pre-release on a new instance. Coming from 4.18, read [Upgrading from 4.18](/en/docs/upgrade-4-18/).

## Install or move to the pre-release

Paste this into a terminal:

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app/next)"
```

It is the normal installer, with the same questions. When no pre-release is open, it says so and changes nothing.

It works with the `apt` and `dnf` packages on 64-bit machines, and with Docker. 32-bit machines have no package: use Docker there.

## Get a newer pre-release

Run the same command again. **Settings > Updates** does not offer pre-releases, and never offers to go back to 4.18.

## When the final release is out

**Settings > Updates** offers it. Docker then follows stable releases, with nothing more to do.

With the `apt` or `dnf` package, the machine stays on the pre-release package source after the update: when a later pre-release comes out, `apt upgrade` or `dnf upgrade` would install it. Run the regular install command once to go back to the stable source:

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

## Go back

Two ways:

- Use a new instance, and keep this one as it is.
- Restore your own copy of the data on a fresh 4.18 install: the steps are in [Going back to 4.18](/en/docs/upgrade-4-18/#going-back-to-418).

Never run the 4.18 install script on a machine running a pre-release. It does not know pre-releases, and can start 4.18 on a database it cannot read. Later versions of the installer refuse and change nothing.
