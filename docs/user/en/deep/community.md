---
title: Meshloom Community
description: Join, bind an IATA code, publish overheard packets, and share hashtag names.
level: deep
order: 17
---

Meshloom Community is an optional observer network. When it is on, this server can publish overheard **raw packets** to the official Stats hosts, use the community directory (hop names, locate, observer-reach), and share **hashtag channel names** in a single global list (names are not grouped by airport).

It is not a fanout row you create in Settings > Fanout. Join and leave from **Settings > Community** (`#settings/community`).

## Default for new installs

A **brand-new database** seeds Community on, unless you set `MESHLOOM_COMMUNITY=0` (or `false` / `off`) before the first start. Existing databases are never flipped by that variable: they keep whatever was stored.

Until an IATA airport code is saved, a banner stays on screen. Packet publishing, Live, and hashtag-name publishing wait for that code. The directory (hop names, locate, heard-by) does not need it. An operator who turns Community off can dismiss the banner permanently in that browser.

While `MESHLOOM_COMMUNITY_IATA` is set, its 3-letter code replaces the one saved in the interface on every read; `MESHLOOM_COMMUNITY_BROKER_HOST` and `MESHLOOM_COMMUNITY_API_BASE` do the same for the hosts.

## What leaves the machine

With Community on and IATA set:

- Overheard **raw packets** are published to the official Stats MQTT hosts. Decoded conversation text does not go through this path.
- Hop names, RF locate, and heard-by counts use the community directory. With Community off there is no directory at all: the heard-by window and the radio test say Community is off.
- The airport search in Settings and in the map location picker asks `api.fx-port.com`. It is part of Community: with Community off it is never called, and you type the 3-letter code yourself.
- **Hashtag names** of local hashtag channels (up to 50), and names the channel finder discovers, can be published to the global list. Publishing needs an IATA code set. Keys are not shared.

Turning Community off cuts every connection to the Community hosts (`*.meshloom.app`, or the hosts set by `MESHLOOM_COMMUNITY_BROKER_HOST` and `MESHLOOM_COMMUNITY_API_BASE`) and to the airport search, at once and without a restart:

- the node's `online` status on the broker is cleared before the MQTT connection closes, so the node does not stay listed as online;
- the Live feed closes, and Community requests still in flight are cancelled;
- the update check asks GitHub only, once every 6 hours. With Community on it runs every 5 minutes and falls back to the Community release mirror when GitHub cannot be reached.

Changing the IATA code clears the status left under the old code the same way.

Community off does not mean no outbound traffic at all: the update check (GitHub), Web Push and the fanout modules you configure have their own hosts.

## In the interface

**Settings > Community**: join or leave, type an IATA code (or search for it once joined), see contribution stats.

The channel finder still works offline. It tries a bundled MeshCore name list against stored undecrypted GroupText samples first, then Community names when Community is on and an IATA code is set.

See [Variables and settings](/en/docs/deep/environment/) and [Security](/en/docs/deep/security/).
