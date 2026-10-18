---
title: Plugin isolation
description: How Meshloom keeps a plugin away from your database and your network, and how to check which level your install gets.
level: deep
order: 22
---

Meshloom 5 runs each plugin in a separate process, and the operating system puts walls around that process. How strong the walls are depends on how you installed Meshloom. The level is decided when the server starts, written in the server log, and reported by `/api/health` in `plugins_isolation`.

## The three levels

**Isolated by bubblewrap** (`bwrap`). The Linux package, the Raspberry Pi image and a source install on Linux use [bubblewrap](https://github.com/containers/bubblewrap). The plugin sees only its own files, the Python it runs on and a private scratch folder. It cannot see the database, the rest of Meshloom's files or the server's memory, and it has no network at all: anything it fetches goes through Meshloom, which checks the plugin's permissions. Stopping the plugin stops everything it started.

**Dedicated user** (`setuid`). The Docker image and the Home Assistant add-on run Meshloom as root, and Docker does not allow bubblewrap. Plugins run as a separate account, `meshloom-plugin`, that cannot open the database or read the server's memory. To make that true, Meshloom tightens the permissions of its data folder at startup: the folder becomes `0711` and the files in it `0600` (readable by root only); the plugin folders stay readable. If you open the database from the host with another account, do it as root. At this level a plugin that breaks out of Meshloom's own guards could still reach the network.

**Hooks only** (`none`). Neither of the above is available, so only Meshloom's own guards inside the plugin process remain. They stop honest mistakes, not a determined attacker: a hostile plugin could read the database and Meshloom's files. The server log says so with a warning at every start. This happens when:

- bubblewrap is not installed (the Linux package recommends it; `sudo apt install bubblewrap` or `sudo dnf install bubblewrap` adds it);
- the system refuses the user namespaces bubblewrap needs (Ubuntu 24.04 and later restrict them; a hardened kernel may disable them);
- the Docker image runs with `MESHLOOM_RUN_AS_USER`, which gives up the rights needed to switch to the plugin account;
- Meshloom runs on macOS or Windows.

## Check your level

Look for `Plugin isolation` in the server log at startup, or open `/api/health` while signed in and read `plugins_isolation`: `bwrap`, `setuid` or `none`. After installing bubblewrap, restart Meshloom so it checks again.

Escaping these walls is a security bug: report it as described in the project's security policy. Plugins you install are still code you choose to trust; the walls limit what a mistake or a bad update can reach.
