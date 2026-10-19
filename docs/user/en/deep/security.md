---
title: Security
description: What accounts protect and what they do not, the locks that exist (sign-in, roles, HTTPS, turning bots off, an encrypted backup), and the radio proxy that has no login.
level: deep
order: 20
---

Meshloom is designed for a network whose users you know. Several design choices only make sense under that assumption. These choices are deliberate, not oversights.

## What accounts protect, and what they do not

**Accounts are keys, not walls.** From 5.0 every page and every API route needs a signed-in account, and the server checks its role on each request ([Accounts and access](/en/docs/deep/access/)). That keeps strangers out; it does not limit what a person you let in can do. An administrator can read the whole history, send messages as your node, change the radio's settings and edit bots; a member can send as your node. Give accounts only to people you trust with the node.

**Other web pages are kept out.** The interface works from any device that opens Meshloom's own address. Another web site open in the same browser is a different *origin*: the server gives it no CORS access (same-origin only), so it cannot read the API's answers, and it refuses changes (`POST`, `PUT`, `PATCH`, `DELETE`) that a browser sends from another site, with or without a session. The session cookie is also `SameSite=Lax`. If you use a dashboard or a script served from another address, list that address in **Settings > Local Configuration > Allowed origins** (one `scheme://host[:port]` per line, empty by default, no `*`); each listed site can then do everything the signed-in user can, including the live connection below. Scripts and apps that send no `Origin` (curl, a phone app, a Home Assistant sensor) are not browsers: they sign in with a session or an [API token](/en/docs/deep/access/#api-and-device-tokens).

The live connection (`/api/ws`, the WebSocket) needs the same account and is refused when a browser opens it from a page of another site, so such a page cannot listen to your messages and packets as they arrive. Its `Origin` must match the address the browser asked for (`Host`, or `X-Forwarded-Host` behind a proxy that rewrites `Host`) or be one of the **Allowed origins**, compared the same way as for the API (case, default port and international domain names do not matter); clients that send no `Origin`, such as scripts, are accepted, but still need a token or a session. What a WebSocket receives follows the role: a `viewer`, a `kiosk` or a token without `read_private` never gets the text of a direct message. See [HTTPS](/en/docs/deep/https/#websocket).

**DNS rebinding.** Meshloom also looks at the name in the browser's address. In 5.0.0 an unknown name is only noted; from 5.1 it will be refused unless you declared it (see [Host names Meshloom answers to](/en/docs/deep/https/#host-names-meshloom-answers-to)). A trusted-network rule always checks it strictly.

**Bots run arbitrary Python.** A bot is a small Python program written in the interface. Meshloom runs it as-is, with full access to the language (`exec()` with the complete `__builtins__`). Any administrator can therefore run code on the host machine. This is intentional.

**The radio proxy has no login.** See [Radio proxy](#radio-proxy) below.

None of this weakens MeshCore's own encryption over the air: direct messages and channel messages stay encrypted as usual. These points describe who can use the application, not what travels over the air. The node's private key is the exception on the server side: any administrator can read it (see [The node's private key](#the-nodes-private-key)).

## The warning dialog

While bots are enabled and no account protects the instance (from 5.0 every page needs a signed-in account, so you normally never see it), Meshloom opens a full-screen warning titled "Unprotected bot execution is enabled". It offers two ways out:

- **Disable Bots Until Server Restart** turns the bot system off immediately. It comes back at the next restart unless you turn it off for good (see below).
- Tick the acknowledgement box, then **Do Not Warn Me On This Device Again**. The choice is remembered by this browser only, so another browser or phone sees the warning again.

## Signing in

Signing in is mandatory and uses accounts: a password of at least 12 characters, optionally a second factor (an authenticator app, recovery codes) and passkeys. Wrong passwords slow the next attempts down without ever locking an account, and sensitive actions are written to an audit log that administrators read. All of it is in [Accounts and access](/en/docs/deep/access/). Without HTTPS the password travels in clear text on every sign-in, so pair it with [HTTPS](/en/docs/deep/https/) beyond a network you trust.

Up to 4.18 the only lock was a shared HTTP Basic login (`MESHCORE_BASIC_AUTH_USERNAME` and `MESHCORE_BASIC_AUTH_PASSWORD`). On the first start of 5.0 it becomes the administrator account, then Meshloom stops reading the variables and warns while they are still set: remove them. Scripts that send it with `Authorization: Basic` keep working until 5.2 ([The 4.x Basic login](/en/docs/deep/access/#the-4x-basic-login)). On the Home Assistant add-on, Home Assistant signs you in through the sidebar; its port 8000 is not published by default, and where you publish it a Meshloom account is asked.

## Disable bots

`MESHCORE_DISABLE_BOTS=true` switches the bot system off at startup. No bot runs, the server answers `403` to any attempt to change bots, and the interface shows the feature as unavailable. The Linux package already sets this in `/etc/meshloom/meshloom.env`.

The **Disable Bots Until Server Restart** button of the warning dialog does the same temporarily, without touching the environment. See [Fanout](/en/docs/deep/fanout/) for bots and the other outputs.

## The node's private key

Meshloom reads the radio's private key each time it connects to the radio, if the radio's firmware allows it, and keeps it in the server's memory only. Meshloom never writes it to its database. If the firmware refuses, Meshloom works without it, but it can no longer decrypt direct messages on the server, and a backup then holds no identity.

The key can always be read back by any administrator: `GET /api/radio/private-key` returns it as hexadecimal text, and both the configuration export in **Settings > Radio** and the full backup in **Settings > Database** contain it (see [Back up your radio](/en/docs/backup/)). There is no switch to turn this off. Another web site open in the same browser cannot read them (see [What accounts protect, and what they do not](#what-accounts-protect-and-what-they-do-not)), unless you listed it in **Allowed origins**. Keep Meshloom on a network you trust, and treat the backup file like the key itself: encrypt it with a passphrase when you keep it anywhere else than on that network.

Importing a key (**Set Private Key**, write-only) never shows the key. A key that does not match the radio the database is bound to is refused until you confirm the identity change (see [First launch](/en/docs/first-run/)).

On Linux, the server also marks itself as not dumpable at startup, so another program running as the same user cannot read its memory or its environment (where a 4.x Basic login set in the environment would live). The cost: the server writes no core dump, and debugging tools such as `py-spy` or `gdb` need `sudo` to attach to it. This protects against other non-root programs and does nothing against root; the default Docker image and the Home Assistant add-on run as root, so the protection mainly matters for the deb/rpm packages and installs that run as a dedicated user.

## The sharing key

Link and file sharing (coming in a later version) does not use the radio's key. Meshloom creates a separate key for it, at random, the first time sharing needs it, and keeps it in its database. To the sharing service this key is a pseudonym: it groups your uploads and your quotas, and it is not linked to your radio's public key in the Community directory, so what you share cannot be tied to your node, a ban there has no effect on Community, and a new radio keeps the same sharing identity. Only its public half leaves the server, in the short signed tokens sent to the sharing service: each one is valid for under two minutes, for one request only.

The key never appears in the settings API, the support snapshot, the logs or any backup: the full backup and the JSON backup refuse to include it, and the `meshcore.db` download has it cleared. After a restore, Meshloom gives the instance a new sharing key; what you shared before stays online until it expires, but is no longer listed as yours. Other features that need their own key (remote access) get a separate one, so the two cannot be linked to each other.

## Radio proxy

**Settings > Proxy** can make Meshloom behave like a radio on the network, so a phone app or a second Meshloom can use your radio through it. It is off by default. The protocol it speaks has no authentication, and accounts do not cover it: it listens on every interface (port 5051 by default), and anyone who can reach that port can use your radio, send as your node included. This is a decision, not an oversight: the apps that speak the protocol cannot sign in. Enable it only on a network you trust and never forward the port to the Internet.

## Wi-Fi password

Meshloom never stores the radio's Wi-Fi password, in its database or in its logs, at any log level. To know whether credentials are stored, it reads the password back from the radio over the companion link, then forgets it: after each connection, at each retry of that check, and on a manual **Retry** while no check has completed. Storing credentials sends the password to the radio and reads it back twice (once before, once to verify). Over a TCP transport the companion link is not encrypted: the password crosses your local network in clear each time Meshloom reads it back or stores it, and anyone who can watch that network can read it. Over USB or Bluetooth it does not cross your network. See [Radio, contacts, and channels](/en/docs/deep/radio/#wi-fi).

## Integration and notification secrets

Once saved, the secrets of your integrations and notification destinations are never shown again: the Apprise addresses, the webhook URLs, header values and HMAC secrets, the MQTT passwords, the Amazon SQS access key ID, secret key and session token, and the SMTP password. The API, the support snapshot and the integration statuses show `********` instead; the Apprise list shows only each service and host, a webhook URL only its scheme and host, and a header only its name. An integration's error never quotes the password or the tokens of its Apprise address or webhook URL, nor a header value, only the service and host. Changing where a secret goes (a webhook or notification URL, an MQTT broker, its port or TLS, an SQS endpoint, the SMTP server, its port or security) requires typing the secret again (for a webhook: its header values and HMAC secret), and a restore that points a secret somewhere else drops it with a warning. The JSON backup download (`GET /api/settings/backup/json`) leaves out the SMTP password, the notification webhook URL and secret and the Apprise notification URLs; restoring it keeps the ones already stored. The full backup in **Settings > Database** still holds the secrets of the integrations (MQTT, fanout webhooks, Apprise outputs, SQS): encrypt it with a passphrase. The notification **channels** and **destinations** (e-mail, webhook, Apprise) are the exception: their secrets (passwords, tokens, addresses that carry a token) are never written to any JSON backup, full or not. A restore recreates those channels switched off, marked *Secret missing after a restore*, until you type the secret again; the support snapshot lists each channel with the names of its secrets that are set, never their values.

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

## Trusted networks

A trusted network rule signs a device in without a password (see [Accounts and access](/en/docs/deep/access/#trusted-networks)). It never applies through a reverse proxy, a tunnel, Meshloom Remote or Home Assistant ingress, nor from this machine's loopback outside the add-on, and only when the browser uses an IP address or a local name. Keep the list empty unless you need it, and never map a network to an administrator you would not hand the machine to.

## A reasonable posture

- Give accounts only to people you trust, each the weakest role that does the job; turn on two-factor authentication for administrators, and **Require two-factor authentication for administrators** when Meshloom can be reached from the Internet.
- Never expose port 8000 or the radio proxy port directly to the Internet. For remote access, use a VPN, or a reverse proxy with HTTPS that you list in **Trusted proxies**.
- Set `MESHCORE_DISABLE_BOTS=true` if you do not need bots.
- Encrypt a full backup with a passphrase when you store it away from that network.
- Always serve Meshloom over HTTPS beyond a network you trust: the session cookie and the password travel in clear over plain HTTP.

The short version is in [A trusted network](/en/docs/trust/).
