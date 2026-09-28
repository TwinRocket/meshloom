---
title: Around the messages
description: The map, the visualizer, the packet feed — what you watch without sending anything.
level: start
order: 5
---

A radio hears much more than what is addressed to you. Adverts, echoes, messages from other channels, unreadable packets: everything within range passes through the antenna. Meshloom keeps this material and gives you a few ways to inspect it.

None of this is needed to send a message. It is observation. You can skip it entirely.

On a phone, open them from **Tools**. On a large screen, they sit on the icon rail. The node map is its own destination, next to conversations.

## The node map

The map opens from **Map**, in the bottom bar or on the rail. An advert can include coordinates. When it does, Meshloom places the node on a map. One heard advert, one more point.

Two limits matter. Not every node broadcasts its position: nodes that keep it private do not appear on the map, even if they are active. And a position is from the last advert received, not live tracking — a mobile node appears where it was the last time it spoke.

The map is mainly for understanding the local network’s geography: where the repeaters are, which direction messages travel, and which hill explains why a nearby node cannot be heard.

## The mesh visualizer

The map shows where nodes are. The visualizer shows how packets get there.

Every received packet carries the trace of the hops it crossed. By combining those traces, Meshloom draws the network as it actually works, rather than as a map might suggest. The most-used links stand out quickly.

The benefit is practical: you can work out which repeater carries your traffic. That helps when the repeater goes down and you need to understand why nothing is getting out.

Identities are not always certain. A hop is identified only by part of a key, and two nodes can share that same fragment. The visualizer then shows a hypothesis, not a fact.

## The raw packet feed

The feed shows everything the radio hears as it arrives, without filtering: messages for your channels, messages from channels whose keys you do not have, adverts, receipts, and packets damaged in transit.

It is an observation tool, not a reliable source of information. Think of it as an aquarium: interesting to watch, useful for copying one packet or checking that the radio hears something, and harmless to ignore.

The feed follows the newest packet continuously. A checkbox pauses it so you can inspect an older packet. Click a packet to open its details. A statistics panel summarizes what the session has heard: volumes, packet types, and the busiest nodes.

Packets that could not be decrypted are not necessarily lost. They are what makes later historical decryption possible when a channel key becomes available.

## The rest

- **Route trace** sends a test packet through known repeaters and back to your radio. It measures a route instead of inferring one.
- **Message search** searches the entire stored history, across direct messages and channels. Clicking a result opens the conversation at that exact point, with surrounding context.
- **Statistics**, in Settings, aggregate what the node has seen: volumes, activity by period, and the busiest channels.
- **Channel finder** tries hashtag names against undecrypted GroupText — packets heard in this session and a sample of stored ones. Community names and a bundled MeshCore list are tried when available. It still works with Community off.

## The live feed

**Live** places packets on a map as the radio hears them. It shows activity around you, not only nodes already placed by an advert. Meshloom Community and an IATA code are required to open it.

## The control journal

The **control journal** gathers traffic that is not a conversation: requests, responses, anonymous requests, and group data. When the payload stays encrypted, you see the envelope, not the contents.

## RF locate

**RF locate** draws a zero-hop coverage area from radios that heard the node nearby. It is not a GPS point, and nothing is written onto the contact.

## Discovered channels

A hashtag channel that was heard, or decrypted from a public channel name the community already knows, does not enter your conversations on its own. It appears in **Discovered channels**. You keep it or dismiss it.

## Radio test

**Radio test** sends a message across the network and lists the community radios that heard it. It is available only when Meshloom Community is on.

## It takes space

Keeping raw packets has a cost. They accumulate and the database grows. The **Database** section of Settings shows its size and can purge old packets.

Purging closes the door on historical decryption for the deleted period: messages already decrypted remain, but unreadable packets disappear permanently. On a machine without tight disk limits, leaving it for a few weeks is usually fine.

Before leaving it running, read [A trusted network](/en/docs/trust/).
