---
title: Try the pre-release
description: Install or move to a Meshloom release candidate with one command, and how to go back.
level: start
order: 2.9
---

A pre-release (a release candidate, `5.0.0-rc.1` for example) is the next version before it is final. It can have bugs that a stable release would not. A machine that does not run the command below never receives one.

## Before you start

Copy your data first, or try the pre-release on a new instance. Coming from 4.18, read [Upgrading from 4.18](/en/docs/upgrade-4-18/). What changed and the known limits are in the [release notes](https://github.com/TwinRocket/meshloom/releases).

## Install or move to the pre-release

Paste this into a terminal:

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app/next)"
```

It is the normal installer, with the same questions. When no pre-release is open, it says so and changes nothing.

It works with the `apt` and `dnf` packages on 64-bit machines, and with Docker. 32-bit machines have no package: Docker covers ARMv7 there. ARMv6 and 32-bit x86 have no image.

## Get a newer pre-release

Run the same command again. **Settings > Updates** only looks at stable releases: it does not announce a newer pre-release, and never offers to go back to 4.18.

## When the final release is out

**Settings > Updates** offers it, and the machine stays on the pre-release until you accept.

- **Docker:** the update pins that final release, and from then on the machine follows stable releases.
- **`apt` or `dnf` package:** the machine stays on the pre-release package source. An update, from **Settings > Updates** or with `apt upgrade` or `dnf upgrade`, installs what that source serves. If a later pre-release is already out, you get that one, even though the page offered the final release.

So with a package, run the usual install command once the final is out. It goes back to the stable source. It also installs the final release if the machine is still on a pre-release, with `apt` and with `dnf` (the stable installer upgrades an installed `rpm` package and installs `bubblewrap` if it is missing, so extensions keep their isolation):

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

## Go back

Two ways:

- Use a new instance, and keep this one as it is.
- Restore your own copy of the data on a fresh 4.18 install: the steps are in [Going back to 4.18](/en/docs/upgrade-4-18/#going-back-to-418).

Never run the 4.18 install script on a machine running a pre-release. It does not know pre-releases, and can start 4.18 on a database it cannot read. Later versions of the installer refuse and change nothing.
