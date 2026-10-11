---
title: Meshloom Community
description: Join, bind an IATA code, publish overheard packets, and share hashtag names.
level: deep
order: 17
---

Meshloom Community is an optional network of observers. When it is on, your server can:

- publish the **raw packets** your radio hears to the official Meshloom Stats servers;
- use the **community directory**, which gives names to hops, powers RF locate and the "heard by" counts, and feeds the Live page and the radio test;
- share and look up the names of **hashtag channels** in a single global list.

It is not an integration you create in **Settings → MQTT & Automation**. You join and leave from **Settings → Meshloom Community**.

## Joining

1. Open **Settings → Meshloom Community**.
2. Enter the **IATA code** of the nearest airport: three letters, such as `CDG`. If you do not know it, look it up with **Browse IATA airport codes**: the airport search (**Find an airport**) is a Community feature, so it only appears once you have joined, to change the code.
3. Save the code.

The code is a coarse label for your region, not a precise location. Meshloom compares it with your radio's position and tells you whether the two agree. If you are sure of your choice despite a mismatch, **I am sure** confirms it. Community limits how often the code can be changed.

Until a code is saved, a banner stays on screen. Publishing packets, the Live page, publishing hashtag names and channel look-ups wait for that code. The directory (hop names, RF locate, "heard by") does not need it. If you have turned Community off, you can dismiss the banner for good in that browser with **Don't show again**.

## New installs

A **brand-new database** starts with Community on, unless `MESHLOOM_COMMUNITY=0` (or `false`, `off`, `no`) is set before the first start. Existing databases are never switched by that variable: they keep what they stored.

Three other variables are meant for people who run Meshloom for others:

- `MESHLOOM_COMMUNITY_IATA` replaces the code saved in the interface for as long as it is set, so changing the code in Settings has no effect.
- `MESHLOOM_COMMUNITY_BROKER_HOST` and `MESHLOOM_COMMUNITY_API_BASE` do the same for the two Community servers.

See [Variables and settings](/en/docs/deep/environment/).

## What leaves your machine

With Community on and a code saved, your server sends:

- **Raw packets**, exactly as they travel over the air, together with the signal strength, your radio's name and its public key. Messages for private channels and direct messages stay encrypted. The text of your conversations never goes through this path.
- **A status message**, when the connection opens and then every five minutes: your radio's name, public key, model, firmware version and radio settings (frequency, bandwidth, spreading factor, coding rate), the Meshloom version, and the radio's counters when it provides them.
- **Directory requests.** To name a hop or draw an RF locate zone, your server asks Community about the nodes involved. The browser never talks to Community itself: your server relays. One-byte hops are never sent.
- **Hashtag channel names.** When you create or adopt a hashtag channel, or when Meshloom finds one, its name (up to 50 at a time) is added to the global list. Names are not grouped by airport. Keys are not shared. A channel whose key does not come from its name is private: its name is never shared, even when it starts with `#`.
- **Channel look-ups.** To recognize an unknown channel, Meshloom sends the one-byte identifier of the channel to ask which names match. If nothing matches, it may also send one encrypted packet of that channel (once per identifier while Meshloom runs), so the community can try to find its name. Only channels you cannot read are concerned.

Your radio's private key never leaves your machine. It is only used to sign the short-lived passes that prove to Community who is calling. Your radio's public key is your account: rotating the key of the radio starts a new history and drops the code you bound.

**Turning Community off cuts every connection to Community** (`*.meshloom.app`, or the hosts set by `MESHLOOM_COMMUNITY_BROKER_HOST` and `MESHLOOM_COMMUNITY_API_BASE`) and the airport search, at once and without a restart, with the single exception described in the first point:

- your node's status on the broker turns `offline` and is then cleared, before the MQTT connection closes, so it does not stay listed as online. If the broker cannot be reached at that moment, nothing can be sent: the broker then marks the node `offline` itself once it notices the connection is gone, and that `offline` would stay. This is the **only connection made after you leave**: Meshloom reconnects to the broker, alone, to delete that leftover status and nothing else (no packets, no status, no other request). It tries at most 6 times over about 13 hours (after 1 minute, then 5 minutes, 15 minutes, 1 hour, 4 hours and 8 hours), survives a restart, and gives up for good after 24 hours. Once the status is deleted, or once it gives up, nothing connects to Community any more;
- the Live feed closes, and Community requests still in flight are cancelled;
- the "heard by" counts and the radio test say Community is off instead of showing an empty result.

Changing the IATA code clears the status left under the old code the same way, with the same single retry if the broker cannot be reached. Stopping or restarting Meshloom marks the node `offline`. Joining again with the same code cancels a pending retry: the status is yours to publish again.

**Community off does not mean no network traffic at all.** These still happen:

- the check for new Meshloom versions asks GitHub only, once every 6 hours. With Community on it runs every 5 minutes and falls back to a Community mirror (`/v1/meshloom/latest`) when GitHub cannot be reached;
- Web Push and the integrations you set up call their own servers;
- alert emails leave through the mail server you set up. The Meshloom logo is inside the email, so opening one loads nothing from `meshloom.app`;
- the map backgrounds are loaded by your browser from their providers (OpenStreetMap, CARTO, OpenTopoMap, Esri).

The airport search (`api.fx-port.com`, through your server) is part of Community: with Community off it is never called, and you type the three-letter code yourself.

## In the interface

**Settings → Meshloom Community** lets you join or leave, type the IATA code (or search for it once joined), see whether the publisher is connected, and read your own contribution (unique packets in the last 24 hours and 7 days, and your rank in your region) next to the community's totals.

See [Variables and settings](/en/docs/deep/environment/) and [Security](/en/docs/deep/security/).
