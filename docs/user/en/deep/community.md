---
title: Meshloom Community
description: Join, bind an IATA code, publish overheard packets, and share hashtag names.
level: deep
order: 17
---

Meshloom Community is an optional observer network. When it is on, this server can publish overheard **raw packets** to the official Stats hosts, use the community directory (hop names, locate, observer-reach), and share **hashtag channel names** for the same airport code.

It is not a fanout row you create in Settings > Fanout. Join and leave from **Settings > Community** (`#settings/community`).

## Default for new installs

A **brand-new database** seeds Community on, unless you set `MESHLOOM_COMMUNITY=0` (or `false` / `off`) before the first start. Existing databases are never flipped by that variable: they keep whatever was stored.

Until an IATA airport code is saved, a banner stays on screen. Tokens need that code, so contribution and directory features wait for it. An operator who turns Community off can dismiss the banner permanently in that browser.

`MESHLOOM_COMMUNITY_IATA` can seed a 3-letter code on a new database. `MESHLOOM_COMMUNITY_LOCKED=1` blocks the UI from turning Community on.

## What leaves the machine

With Community on and IATA set:

- Overheard **raw packets** are published to the official Stats MQTT hosts. Decoded conversation text does not go through this path.
- Hop names, RF locate, and heard-by counts use the community directory. With Community off there is no directory at all: Meshloom contacts no other host.
- **Hashtag names** of local hashtag channels (up to 50), and names the channel finder discovers, can be published for that IATA. Keys are not shared.

One opt-out stops publish and community directory calls.

## In the interface

**Settings > Community**: join or leave, search or type an IATA code, see contribution stats.

The channel finder still works offline. Community names, when available, are tried first along with a bundled MeshCore name list and stored undecrypted GroupText samples.

See [Variables and settings](/en/docs/deep/environment/) and [Security](/en/docs/deep/security/).
