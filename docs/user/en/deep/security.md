---
title: Security
description: What Meshloom does not protect, and the few locks that exist: Basic auth, turning bots off, an encrypted backup.
level: deep
order: 20
---

Meshloom is designed for a network whose users you know. Several design choices only make sense under that assumption. These choices are deliberate, not oversights.

## What does not exist

**No user accounts.** There are no logins, sessions, roles or per-feature permissions. Anyone who can reach the server can read the history, send messages as your node, change radio settings and edit bots.

**Other web pages get only partial protection.** The interface works from any device that opens Meshloom's own address. Another web site open in the same browser is a different *origin*: the server gives it no CORS access (same-origin only), so it cannot read the API's answers or send it JSON requests. It can still fire requests a browser sends without asking first (a `POST` with no body, such as a reboot) without seeing the answer. If you use a dashboard or a script served from another address, list that address in **Settings > Local Configuration > Allowed origins** (one `scheme://host[:port]` per line, empty by default, no `*`). Each listed site can do everything you can, with your credentials, including the live connection below.

The live connection (`/api/ws`, the WebSocket) is the exception: the server refuses it when a browser opens it from a page of another site, so such a page cannot listen to your messages and packets as they arrive. Its `Origin` must match the address the browser asked for (`Host`, or `X-Forwarded-Host` behind a proxy that rewrites `Host`) or be one of the **Allowed origins**, compared the same way as for the API (case, default port and international domain names do not matter); clients that send no `Origin`, such as scripts, are accepted. See [HTTPS](/en/docs/deep/https/#websocket).

**Bots run arbitrary Python.** A bot is a small Python program written in the interface. Meshloom runs it as-is, with full access to the language (`exec()` with the complete `__builtins__`). Anyone who can reach Meshloom can therefore run code on the host machine. This is intentional.

None of this weakens MeshCore's own encryption over the air: direct messages and channel messages stay encrypted as usual. These points describe who can use the application, not what travels over the air. The node's private key is the exception on the server side: anyone who reaches the interface can read it (see [The node's private key](#the-nodes-private-key)).

## The warning dialog

While bots are enabled and no Basic auth is set, Meshloom opens a full-screen warning titled "Unprotected bot execution is enabled". It offers two ways out:

- **Disable Bots Until Server Restart** turns the bot system off immediately. It comes back at the next restart unless you turn it off for good (see below).
- Tick the acknowledgement box, then **Do Not Warn Me On This Device Again**. The choice is remembered by this browser only, so another browser or phone sees the warning again.

## HTTP Basic authentication

```text
MESHCORE_BASIC_AUTH_USERNAME=...
MESHCORE_BASIC_AUTH_PASSWORD=...
```

Set both, or neither: the server refuses to start with only one. Basic auth protects the pages, the API and the live connection (WebSocket). It is a single shared login with no roles. Without HTTPS the password travels in clear text, so pair it with [HTTPS](/en/docs/deep/https/).

From Meshloom 5.0, signing in is mandatory and uses accounts. On the first start of 5.0, these two variables become the administrator account (same name, same password), then Meshloom no longer reads them and logs a warning while they are still set: remove them. Scripts and Home Assistant sensors that send this user name and password with Basic auth keep working until 5.2. Known limitation of the 5.0 pre-release: the Home Assistant add-on cannot keep you signed in through the sidebar yet; open it on its own port (8000) until the Home Assistant sign-in arrives, which 5.0.0 requires. A browser page from another site can no longer change anything with your login: Meshloom refuses changes that come from another site.

## Disable bots

`MESHCORE_DISABLE_BOTS=true` switches the bot system off at startup. No bot runs, the server answers `403` to any attempt to change bots, and the interface shows the feature as unavailable. The Linux package already sets this in `/etc/meshloom/meshloom.env`.

The **Disable Bots Until Server Restart** button of the warning dialog does the same temporarily, without touching the environment. See [Fanout](/en/docs/deep/fanout/) for bots and the other outputs.

## The node's private key

Meshloom reads the radio's private key each time it connects to the radio, if the radio's firmware allows it, and keeps it in the server's memory only. Meshloom never writes it to its database. If the firmware refuses, Meshloom works without it, but it can no longer decrypt direct messages on the server, and a backup then holds no identity.

The key can always be read back by anyone who reaches the interface: `GET /api/radio/private-key` returns it as hexadecimal text, and both the configuration export in **Settings > Radio** and the full backup in **Settings > Database** contain it (see [Back up your radio](/en/docs/backup/)). There is no switch to turn this off. Another web site open in the same browser can no longer read them (see [What does not exist](#what-does-not-exist)), unless you listed it in **Allowed origins**. Keep Meshloom on a network you trust, and treat the backup file like the key itself: encrypt it with a passphrase when you keep it anywhere else than on that network.

Importing a key (**Set Private Key**, write-only) never shows the key. A key that does not match the radio the database is bound to is refused until you confirm the identity change (see [First launch](/en/docs/first-run/)).

On Linux, the server also marks itself as not dumpable at startup, so another program running as the same user cannot read its memory or its environment (where the Basic auth login lives). The cost: the server writes no core dump, and debugging tools such as `py-spy` or `gdb` need `sudo` to attach to it. This protects against other non-root programs and does nothing against root; the default Docker image and the Home Assistant add-on run as root, so the protection mainly matters for the deb/rpm packages and installs that run as a dedicated user.

## The sharing key

Link and file sharing (coming in a later version) does not use the radio's key. Meshloom creates a separate key for it, at random, the first time sharing needs it, and keeps it in its database. To the sharing service this key is a pseudonym: it groups your uploads and your quotas, and it is not linked to your radio's public key in the Community directory, so what you share cannot be tied to your node, a ban there has no effect on Community, and a new radio keeps the same sharing identity. Only its public half leaves the server, in the short signed tokens sent to the sharing service: each one is valid for under two minutes, for one request only.

The key never appears in the settings API, the support snapshot, the logs or any backup: the full backup and the JSON backup refuse to include it, and the `meshcore.db` download has it cleared. After a restore, Meshloom gives the instance a new sharing key; what you shared before stays online until it expires, but is no longer listed as yours. Other features that need their own key (remote access) get a separate one, so the two cannot be linked to each other.

## Radio proxy

**Settings > Proxy** can make Meshloom behave like a radio on the network, so a phone app or a second Meshloom can use your radio through it. It is off by default. The protocol it speaks has no authentication, and Basic auth does not cover it. Enable it only on a network you trust.

## Wi-Fi password

Meshloom never stores the radio's Wi-Fi password, in its database or in its logs, at any log level. To know whether credentials are stored, it reads the password back from the radio over the companion link, then forgets it: after each connection, at each retry of that check, and on a manual **Retry** while no check has completed. Storing credentials sends the password to the radio and reads it back twice (once before, once to verify). Over a TCP transport the companion link is not encrypted: the password crosses your local network in clear each time Meshloom reads it back or stores it, and anyone who can watch that network can read it. Over USB or Bluetooth it does not cross your network. See [Radio, contacts, and channels](/en/docs/deep/radio/#wi-fi).

## Integration and notification secrets

Once saved, the secrets of your integrations and notification destinations are never shown again: the Apprise addresses, the webhook URLs, header values and HMAC secrets, the MQTT passwords, the Amazon SQS access key ID, secret key and session token, and the SMTP password. The API, the support snapshot and the integration statuses show `********` instead; the Apprise list shows only each service and host, a webhook URL only its scheme and host, and a header only its name. An integration's error never quotes the password or the tokens of its Apprise address or webhook URL, nor a header value, only the service and host. Changing where a secret goes (a webhook or notification URL, an MQTT broker, its port or TLS, an SQS endpoint, the SMTP server, its port or security) requires typing the secret again (for a webhook: its header values and HMAC secret), and a restore that points a secret somewhere else drops it with a warning. The JSON backup download (`GET /api/settings/backup/json`) leaves out the SMTP password, the notification webhook URL and secret and the Apprise notification URLs; restoring it keeps the ones already stored. The full backup in **Settings > Database** still holds them, with the integrations: encrypt it with a passphrase.

## Support snapshot

**Settings > About > Open debug support snapshot** (`/api/debug`) is meant to be pasted into a bug report. Outside the logs it holds versions, settings and counters, never the private key; the keys of contacts and channels appear only as one-way fingerprints. The logs never contain the private key or a Wi-Fi password, at any log level, `DEBUG` included. The logs can contain channel names and channel keys at any level; at `DEBUG` they can also contain repeater and room login passwords and the Bluetooth PIN. Stop copying at the line `STOP COPYING HERE` to leave the logs out.

## What leaves your machine

What each output sends depends on the scope you give it in **Settings > MQTT & Automation**.

- **Community Sharing** only ever carries raw radio packets to a community MQTT server, never readable conversation text.
- **Private MQTT**, **Webhook**, **Apprise** and **Amazon SQS** can carry the full text of messages, according to the channels and contacts you select.
- **Apprise notifications** (Settings > Notifications) send the notifications you chose to the services in your URLs. By default the text of direct messages and private channels stays home (title only, "Message from Léa"); with *Always*, it leaves too. When you forward other people's messages to such a service, you are the one responsible for that data (in the EU, the data controller). Public services such as ntfy.sh keep messages for a while (12 hours by default on ntfy.sh) and anyone who knows the topic can read it: choose a long, random topic, or your own server.
- **Map Upload** sends the adverts of repeaters and room servers, with their position, to a map service (map.meshcore.io or one you choose).
- **Home Assistant** publishes your devices to the MQTT server (broker) you configure.

The notification webhook and Apprise URLs, Web Push and the fanout Webhook, Apprise, Amazon SQS and Map Upload outputs only reach destinations on the Internet unless you allow a private network for them (see [Notifications](/en/docs/deep/push/) and [Fanout](/en/docs/deep/fanout/#destinations-on-your-network)): a page that changes these settings cannot make the server probe your network. Meshloom never follows a redirect for them and always refuses cloud metadata addresses. The webhooks, Web Push and Map Upload connect only to the address that was checked. Apprise and Amazon SQS connect on their own after the check, so someone who controls the DNS of a host name you typed could still point it elsewhere; Apprise accepts only the services listed in [Fanout](/en/docs/deep/fanout/#apprise).

Some features need Internet access: [push notifications](/en/docs/deep/push/), and [Meshloom Community](/en/docs/deep/community/) when joined (raw-packet publishing, directory, sharing of hashtag channel names). Even with both off, Meshloom checks GitHub for new releases once every 6 hours (with Community on: every five minutes, with the Community mirror as a fallback). It keeps working if that check fails. When you leave Community, Meshloom sends it one last request to record the opt-out and delete your radio's position. After that, it is reached again only if Community surely did not receive that request and the status of your node could not be deleted from the broker at that moment (a single reconnect deletes it, a few tries at most within 24 hours, then nothing), or when you press **Retry** because Community did not confirm the opt-out (see [Meshloom Community](/en/docs/deep/community/#leaving)). Alert e-mails carry the Meshloom logo inside them, so opening one loads nothing from the Internet.

## A reasonable posture

- Keep Meshloom on a network where you know the users.
- Never expose it directly to the Internet. For remote access, use a VPN or a tunnel that adds its own login.
- Set `MESHCORE_DISABLE_BOTS=true` if you do not need bots.
- Encrypt a full backup with a passphrase when you store it away from that network.
- Always put Basic auth behind HTTPS.

The short version is in [A trusted network](/en/docs/trust/).
