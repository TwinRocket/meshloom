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

While `MESHLOOM_COMMUNITY_IATA` is set, its 3-letter code replaces the one saved in the interface on every read; `MESHLOOM_COMMUNITY_BROKER_HOST` and `MESHLOOM_COMMUNITY_API_BASE` do the same for the hosts. `MESHLOOM_COMMUNITY_LOCKED=1` blocks the UI from turning Community on.

## What leaves the machine

With Community on and IATA set:

- Overheard **raw packets** are published to the official Stats MQTT hosts. Decoded conversation text does not go through this path.
- Hop names, RF locate, and heard-by counts use the community directory. With Community off there is no directory at all. Community off does not mean no outbound traffic: the update check still asks GitHub, then the Community release mirror (`/v1/meshloom/latest`) as a fallback; the IATA search in Settings asks `api.fx-port.com`; Web Push and fanout modules you configure have their own hosts.
- **Hashtag names** of local hashtag channels (up to 50), and names the channel finder discovers, can be published to the global list. Publishing needs an IATA code set. Keys are not shared.

One opt-out stops publish and community directory calls. It does not stop the update check.

## In the interface

**Settings > Community**: join or leave, search or type an IATA code, see contribution stats.

The channel finder still works offline. It tries a bundled MeshCore name list against stored undecrypted GroupText samples first, then Community names when Community is on and an IATA code is set.

See [Variables and settings](/en/docs/deep/environment/) and [Security](/en/docs/deep/security/).
