---
title: A trusted network
description: No accounts, Python bots, and why Meshloom belongs on a network you know.
level: start
order: 6
---

Meshloom assumes it runs on a network whose users you know. This is not an oversight, it is the design. It helps to understand what that means.

## There are no accounts

There are no user accounts, sessions, roles or per-feature permissions. Anyone who can reach the server on port `8000` gets the full interface without typing anything.

That means reading the whole history of direct messages and channels, writing as your node, changing its name and radio settings, deleting contacts and emptying the database. There is no middle ground between "cannot open the page" and "controls everything".

The server also accepts requests from any web page. This is deliberate: it lets you open the interface from any device on the network with no extra setup. It relies on the same trusted network.

## Bots run code

Meshloom can run bots: small programs that react to the messages you receive. They are written in Python, and Meshloom runs them as they are, with no sandbox and no list of allowed instructions.

The consequence is direct: **anyone who can reach Meshloom can make the host machine run any code.** Not only inside the application, but on the machine, with the server's permissions. It is an intentional automation feature, and it is also the most sensitive part of an installation.

While bots are enabled and no password is set, Meshloom opens a warning dialog ("Unprotected bot execution is enabled"). You can disable bots until the next restart from that dialog, or tick the acknowledgement and dismiss it for this browser. It is not decoration.

Two protections exist:

- Bots are **on by default**, except in the Linux package and the Raspberry Pi image, which turn them off (`MESHCORE_DISABLE_BOTS=true` in `/etc/meshloom/meshloom.env`). The installer asks nothing about this. Docker installs and the Home Assistant add-on keep bots on (the add-on has a `disable_bots` option).
- Setting `MESHCORE_DISABLE_BOTS=true` turns the whole bot system off at startup. No bot runs, changes to bots are refused, and the interface shows the feature as disabled.

If people you do not all know can reach the instance, keep bots disabled.

## The optional password

Meshloom can ask for a user name and a password before showing anything. The installer does not set this up. You configure it with two environment variables, always together:

```
MESHCORE_BASIC_AUTH_USERNAME
MESHCORE_BASIC_AUTH_PASSWORD
```

This is a single shared login, not user accounts: one login for everyone, and whoever has it has everything. It is a simple gate, useful to stop a random device on the network from opening the interface by accident. It is not a permissions system.

It also **needs HTTPS**. Over plain HTTP, the user name and password travel unencrypted on every request. [HTTPS](/en/docs/deep/https/) explains how to set up a certificate, even a self-signed one.

## In practice

A few rules prevent most problems:

- **Do not expose port `8000` to the Internet.** Do not forward it on your router. For remote access, use a VPN into your home or office network.
- On a shared network (shared house, office, guest network), set the password and keep bots disabled.
- Remember that the radio's private key is given to the server so it can decrypt direct messages. It is kept in memory only, and reading it back through the API is disabled unless you turn it on. Still, a compromised machine is a compromised machine.
- If you turn on **Settings > Proxy** to share your radio with other apps, know that it has no login at all, and that Basic auth does not cover it.
- Treat channels for what they are. The key is the only access: giving it to someone lets them read and write there.

The details of the security settings, self-signed certificates and related variables are in [Security](/en/docs/deep/security/).
