---
title: A trusted network
description: No accounts, Python bots, and why this stays on a network you know.
level: start
order: 6
---

Meshloom assumes it runs on a network whose occupants you know. That is not an omission; it is the model. It helps to understand what that means.

## There are no accounts

There are no user accounts, sessions, roles, or per-feature permissions. Anyone who can reach the server at port `8000` gets the full interface without entering anything.

That means reading the complete history of direct messages and channels, writing as your node, changing its name and radio settings, deleting contacts, and purging the database. There is no middle ground between “cannot open the page” and “controls everything”.

The server also applies no origin restriction to requests: any web page can call its API. This is deliberate — it lets you open the interface from any device on the network without extra configuration — and it requires that same trusted network.

While bots are enabled and no password is set, the interface shows a warning about this posture until you acknowledge it. It is not decorative.

## Bots execute code

Meshloom can trigger bots: small programs that react to received messages. They are written in Python and executed as-is on the machine, without a sandbox or an allowlist of permitted instructions.

The consequence is direct: **anyone who can reach Meshloom can make the host machine execute arbitrary code.** Not just inside the application — on the machine, with the server’s permissions. This is an intentional automation feature, and it is also the most sensitive part of the installation.

Two safeguards:

- Bots are **enabled by default**. Only the Linux package turns them off (`MESHCORE_DISABLE_BOTS=true` in `/etc/meshloom/meshloom.env`); the installer asks nothing about them, and Docker installs keep them on.
- The environment variable `MESHCORE_DISABLE_BOTS=true` turns off the bot system at startup. No bot runs, the related settings are rejected, and the interface shows the feature as disabled.

If people you do not all know can reach the instance, keep bots disabled.

## The optional password

Meshloom can require a username and password before opening anything. The installation script does not set this up; configure it with two environment variables, always together:

```
MESHCORE_BASIC_AUTH_USERNAME
MESHCORE_BASIC_AUTH_PASSWORD
```

This is one shared login, not user accounts: one credential for everyone, and whoever has it has everything. It is a basic gate, useful for stopping a random device on the network from opening the interface by accident. It is not an authorization model.

It also **requires HTTPS**. Over plain HTTP, the username and password travel unencrypted on every request across the network. Setting up a certificate, including a self-signed one, is covered in [HTTPS](/en/docs/deep/https/).

## In practice

A few rules prevent most trouble:

- **Do not expose port `8000` to the internet.** Do not forward the port on your router. For remote access, use a VPN into the local network.
- On a shared network — house share, office, guest network — enable the password and keep bots disabled.
- Remember that the radio’s private key is given to the server so it can decrypt direct messages. It is kept in memory only, and API export is disabled unless explicitly enabled. But a compromised machine is still a compromised machine.
- Treat channels as what they are. The key is the only access: sharing it gives someone the future history and the ability to write.

The details of the security settings, self-signed certificates, and related variables are in [Security](/en/docs/deep/security/).
