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
3. Click **Join Meshloom Community**.

Joining is the only way to turn Community on from the settings: there is no on/off switch, and **Leave Meshloom Community** (shown once joined) only turns it off. Meshloom first registers the code with Community, and only then turns Community on and starts publishing. If Community refuses the code (for instance because this radio already changed its code too often in the last 7 days), Community stays off, your saved code does not change, and the settings say why.

The code is a coarse label for your region, not a precise location. Meshloom compares it with your radio's position and tells you whether the two agree. If you are sure of your choice despite a mismatch, **I am sure** confirms it. Community limits how often the code can be changed. Community drops that confirmation when your radio's position moves more than 100 km (by default: the `IATA_GPS_HYSTERESIS_KM` setting on the Community side) from the one it recorded: since Meshloom now sends a new position on its own (see below), this can happen without anything done in the settings, and **I am sure** shows again if the code and the position still disagree.

The code is also the region the Live page shows by default; you can add other regions there.

Until a code is saved, a banner stays on screen. Publishing packets, the Live page, publishing hashtag names and channel look-ups wait for that code. The directory (hop names, RF locate, "heard by") does not need it. While Community is off (as on a new install, or after you leave), you can dismiss the banner for good in that browser with **Don't show again**.

## Waiting for a position

Community needs to know roughly where your radio is: it places each observer on its map to show where packets are heard. While Community is on but your radio has **no position** (none set, or MeshCore's "no GPS" `0, 0`), Community **waits for your position**:

- a banner says "Meshloom Community needs your position to work", on every page, the settings included;
- your server keeps its connection to the Community broker only to publish its **status**, marked as waiting for a position, so Community knows the observer is there;
- **no packet is published**, and the Live page, the directory (hop names, RF locate, "heard by"), the radio test, the statistics, the hashtag names and channel look-ups, the airport search and the Community release mirror are all off, as if you had left.

You can join while your radio has no position: Community then starts in this state. From the banner:

- **Choose my position** opens a map. The point you pick is saved on your radio (as in **Settings → Radio**). As soon as the radio reports it, Meshloom sends it to Community with your code (rounded to about 1 km unless your adverts broadcast it, see below), publishes its status again, and everything above comes back, without a restart.
- **Learn more** explains why, and offers the same choice or **Leave Community**, which is the usual opt-out.

If the radio is disconnected, or not yet recognised after a reconnection, Meshloom keeps the last state it knew: the banner does not come and go while the radio reconnects. Removing the position from the radio (setting it back to `0, 0`) puts Community back in the waiting state at once.

## New installs

A **brand-new database** starts with Community off: joining is your choice, made in **Settings → Meshloom Community**. No environment variable turns it on, sets the code or changes the Community servers, which are fixed (`mqtt.meshloom.app` and `api.meshloom.app`).

`MESHLOOM_COMMUNITY`, `MESHLOOM_COMMUNITY_IATA`, `MESHLOOM_COMMUNITY_BROKER_HOST` and `MESHLOOM_COMMUNITY_API_BASE` are no longer read. On an existing installation that still sets `MESHLOOM_COMMUNITY_IATA`, its code is copied into the settings once, at the first start after the upgrade, so Community keeps the region it knows; whether Community is on stays as it was. Meshloom then logs a warning at each start while one of these variables is still set: remove them. See [Variables and settings](/en/docs/deep/environment/).

## What leaves your machine

With Community on and a code saved, your server sends:

- **Raw packets**, exactly as they travel over the air, together with the signal strength, your radio's name and its public key. Messages for private channels and direct messages stay encrypted. The text of your conversations never goes through this path.
- **Your radio's position**, if it has one, with the code: when you join or change the code, and again on its own when the radio reports a new position or a new **Advert Location Source** (re-sending it is not a code change). The radio reports it when it connects or reconnects, when Meshloom sets its position and when that setting changes, not at every GPS move: a radio moving with its GPS on sends its new position at its next reconnection or setting change. It is **exact only if the radio already broadcasts it** in its adverts (**Include Node Location**). Otherwise it is **rounded to about 1 km** (two decimals). Community uses it to check the code, and **shows it on its public map** (Live, directory): the exact one is the position your adverts already make public, the rounded one places your observer near its town, not at its address. A radio with no position sends none, and Community then waits for one (see [Waiting for a position](#waiting-for-a-position)).
- **A status message**, when the connection opens and then every five minutes: your radio's name, public key, model, firmware version and radio settings (frequency, bandwidth, spreading factor, coding rate), the Meshloom version, and the radio's counters when it provides them. While Community waits for a position, it also says so (`state: waiting_for_position`), and it is published again as soon as that changes.
- **Directory requests.** To name a hop or draw an RF locate zone, your server asks Community about the nodes involved. The browser never talks to Community itself: your server relays. One-byte hops are never sent.
- **Hashtag channel names.** When you create or adopt a hashtag channel, or when Meshloom finds one, its name (up to 50 at a time) is added to the global list. Names are not grouped by airport. Keys are not shared. A channel whose key does not come from its name is private: its name is never shared, even when it starts with `#`.
- **Channel look-ups.** To recognize an unknown channel, Meshloom sends the one-byte identifier of the channel to ask which names match. If nothing matches, it may also send one encrypted packet of that channel (once per identifier while Meshloom runs), so the community can try to find its name. Only channels you cannot read are concerned.

Your radio's private key never leaves your machine. It is only used to sign the short-lived passes that prove to Community who is calling. Your radio's public key is your account: rotating the key of the radio starts a new history and drops the code you bound.

**Turning Community off cuts every connection to Community** (`*.meshloom.app`) and the airport search, at once and without a restart, with the single exception described in the first point:

- your node's status on the broker turns `offline` and is then cleared, before the MQTT connection closes, so it does not stay listed as online. If the broker cannot be reached at that moment, nothing can be sent: the broker then marks the node `offline` itself once it notices the connection is gone, and that `offline` would stay. This is the **only connection made after you leave**: Meshloom reconnects to the broker, alone, to delete that leftover status and nothing else (no packets, no status, no other request). It tries at most 6 times over about 13 hours (after 1 minute, then 5 minutes, 15 minutes, 1 hour, 4 hours and 8 hours), survives a restart, and gives up for good after 24 hours. Once the status is deleted, or once it gives up, nothing connects to Community any more;
- the Live feed closes, and Community requests still in flight are cancelled;
- the "heard by" counts and the radio test say Community is off instead of showing an empty result.

Changing the IATA code clears the status left under the old code the same way, with the same single retry if the broker cannot be reached: first the old status is cleared, then Community records the new code, and only then does Meshloom reconnect under it. If Community refuses the new code, your node goes back online under the old one. Stopping or restarting Meshloom marks the node `offline`. If the code changes while Meshloom is stopped (a restored database, for instance), or Community is turned off meanwhile, the status left under the old code is cleared at the next start, with the same single retry. The same happens once after the upgrade on an installation that used its own broker through `MESHLOOM_COMMUNITY_BROKER_HOST`: the status left on that broker is cleared, then Meshloom publishes on `mqtt.meshloom.app`. Joining again with the same code cancels a pending retry: the status is yours to publish again.

**Community off does not mean no network traffic at all.** These still happen:

- the check for new Meshloom versions asks GitHub only, once every 6 hours. With Community on it runs every 5 minutes and falls back to a Community mirror (`/v1/meshloom/latest`) when GitHub cannot be reached;
- Web Push and the integrations you set up call their own servers;
- alert emails leave through the mail server you set up. The Meshloom logo is inside the email, so opening one loads nothing from `meshloom.app`;
- the map backgrounds are loaded by your browser from their providers (OpenStreetMap, CARTO, OpenTopoMap, Esri).

The airport search (`api.fx-port.com`, through your server) is part of Community: with Community off it is never called, and you type the three-letter code yourself. The Live page also uses it to place the chosen regions on the map, once per code while the page stays open, and only while Community is on.

## In the interface

**Settings → Meshloom Community** lets you join or leave, type the IATA code (or search for it once joined), see whether the publisher is connected, and read your own contribution (unique packets in the last 24 hours and 7 days, and your rank in your region) next to the community's totals. While Community waits for a position, the settings say so and show neither the statistics nor the airport search.

The "heard by" details and the radio test measure distances only to observers whose exact position is known. An observer that publishes a rounded position still appears on their maps, but has no distance and never sets the farthest-ear record.

See [Variables and settings](/en/docs/deep/environment/) and [Security](/en/docs/deep/security/).
