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
2. Enter the **IATA code** of the nearest airport: three letters, such as `CDG`. If you do not know it, type a city in **Find an airport**.
3. Save the code.

The code is a coarse label for your region, not a precise location. Meshloom compares it with your radio's position and tells you whether the two agree. If you are sure of your choice despite a mismatch, **I am sure** confirms it. Community limits how often the code can be changed.

Until a code is saved, a banner stays on screen. Publishing packets, the Live page and publishing hashtag names wait for that code. The directory (hop names, RF locate, "heard by") does not need it. If you have turned Community off, you can dismiss the banner for good in that browser with **Don't show again**.

## New installs

A **brand-new database** starts with Community on, unless `MESHLOOM_COMMUNITY=0` (or `false`, `off`, `no`) is set before the first start. Existing databases are never switched by that variable: they keep what they stored.

Three other variables are meant for people who run Meshloom for others:

- `MESHLOOM_COMMUNITY_IATA` replaces the code saved in the interface for as long as it is set, so changing the code in Settings has no effect.
- `MESHLOOM_COMMUNITY_BROKER_HOST` and `MESHLOOM_COMMUNITY_API_BASE` do the same for the two Community servers.
- `MESHLOOM_COMMUNITY_LOCKED=1` stops the interface from turning Community on.

See [Variables and settings](/en/docs/deep/environment/).

## What leaves your machine

With Community on and a code saved, your server sends:

- **Raw packets**, exactly as they travel over the air, together with the signal strength, your radio's name and its public key. Messages for private channels and direct messages stay encrypted. The text of your conversations never goes through this path.
- **A status message**, when the connection opens and then every five minutes: your radio's name, public key, model, firmware version and radio settings (frequency, bandwidth, spreading factor, coding rate), the Meshloom version, and the radio's counters when it provides them.
- **Directory requests.** To name a hop or draw an RF locate zone, your server asks Community about the nodes involved. The browser never talks to Community itself: your server relays. One-byte hops are never sent.
- **Hashtag channel names.** When you create or adopt a hashtag channel, or when Meshloom finds one, its name (up to 50 at a time) is added to the global list. Names are not grouped by airport. Keys are not shared.
- **Channel look-ups.** To recognize an unknown channel, Meshloom sends the one-byte identifier of the channel to ask which names match. If nothing matches, it may also send one encrypted packet of that channel (once per identifier while Meshloom runs), so the community can try to find its name. Only channels you cannot read are concerned.

Your radio's private key never leaves your machine. It is only used to sign the short-lived passes that prove to Community who is calling. Your radio's public key is your account: rotating the key of the radio starts a new history and drops the code you bound.

**Community off does not mean no network traffic at all.** Turning Community off stops publishing and every directory request. These still happen:

- the check for new Meshloom versions asks GitHub, then, as a fallback, a Community mirror (`/v1/meshloom/latest`);
- the airport search in Settings asks `api.fx-port.com`, through your server;
- Web Push and the integrations you set up call their own servers;
- the map backgrounds are loaded by your browser from their providers (OpenStreetMap, CARTO, OpenTopoMap, Esri).

## In the interface

**Settings → Meshloom Community** lets you join or leave, search for or type the IATA code, see whether the publisher is connected, and read your own contribution (unique packets in the last 24 hours and 7 days, and your rank in your region) next to the community's totals.

See [Variables and settings](/en/docs/deep/environment/) and [Security](/en/docs/deep/security/).
