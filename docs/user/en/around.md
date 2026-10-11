---
title: Around the messages
description: The map, the visualizer, the packet feed — what you watch without sending anything.
level: start
order: 5
---

A radio hears much more than what is addressed to you. Adverts (a node announcing itself), echoes, messages from other channels, unreadable packets: everything within range passes through the antenna. Meshloom keeps this material and gives you a few ways to look at it.

None of this is needed to send a message. It is observation. You can skip it entirely.

On a phone, open these tools from **Tools** in the bottom bar. Each one comes with a one-line description. On a large screen, they sit on the icon rail on the left, and you can reorder or remove them in **Settings → Navigation**. The node map is its own destination, next to your conversations.

## The node map

Open the map with **Map**, in the bottom bar or on the rail. An advert can include coordinates. When it does, Meshloom places the node on the map. One advert heard, one more point.

Two limits matter:

- Not every node shares its position. A node that keeps it private is not on the map, even if it is very active.
- A position is the one from the last advert received, not live tracking. A moving node is shown where it was the last time it spoke.

A few options help you read the map:

- A **Since** filter shows only the nodes heard after a given time: last hour, 24 hours, 3 days, 7 days, a date you choose (**Custom**), or **All**. By default the map shows the last 7 days, so a node that has been silent for longer does not appear until you pick **All**. The browser remembers your choice.
- **Internet relays** is a tick box that adds relays known to Meshloom Community. It only appears when Community is on.
- The background can be switched between light, dark, topographic and satellite.

The map is mainly for understanding the geography of your local network: where the repeaters are, which direction messages travel, and which hill explains why a nearby node cannot be heard.

## The mesh visualizer

The map shows where nodes are. The visualizer shows how packets get there.

Every received packet carries the trace of the repeaters (hops) it crossed. Meshloom combines these traces while the visualizer is open and draws the network as a 3D graph, as it works in practice rather than as a map suggests. The most-used links stand out quickly.

The benefit is practical: you can work out which repeater carries your traffic. That helps when the repeater goes down and you need to understand why nothing is getting out.

Identities are not always certain. A hop is identified by only a small part of a key (often a single byte), and two nodes can share that same fragment. The visualizer then shows a hypothesis, not a fact, and marks these nodes as ambiguous.

## The raw packet feed

The **Packet Feed** shows everything the radio hears as it arrives, without filtering: messages for your channels, messages from channels whose key you do not have, adverts, receipts, and packets damaged in transit.

It is an observation tool, not a reliable source of information. Think of it as an aquarium: interesting to watch, useful for copying one packet or checking that the radio hears something, and harmless to ignore.

- The list follows the newest packet. Untick **Autoscroll** to pause it and read an older packet.
- Click a packet to open its details.
- **Show Stats** opens a panel that summarizes what was heard in a time window (1, 5, 10 or 30 minutes, or the whole session): packets per minute, packet types, signal strength, the most-heard neighbors.
- **Analyze Packet** lets you paste the hexadecimal text of a packet and inspect it.

Packets that could not be decrypted are not necessarily lost. They are what makes decrypting old messages possible later, when you obtain the key of a channel.

## The rest

- **Trace** sends a test packet through repeaters you choose and back to your radio, and measures the route instead of guessing it. You must be able to hear the last repeater of the chain for the trace to succeed.
- **Message Search** searches the whole stored history, direct messages and channels together. Clicking a result opens the conversation at that exact point, with the messages around it. You can narrow the search with `user:` or `channel:` followed by a name or a key. Put names that contain spaces in quotes.
- **Statistics**, in Settings, summarize what the node has seen: contacts, repeaters, channels and messages, packets per hour over 72 hours, the busiest channels over 24 hours, the noise level, and how much traffic uses regional scopes.

## The live feed

**Live** draws packets on a map as they are heard. It shows packets heard by Meshloom Community observers as well as by your own radio, so you see activity far beyond your own range. You can filter by region code, hide packet types, keep only the traces whose route is certain, and choose a sound theme.

Your own radio's packets always appear. The packets of Community observers need Meshloom Community to be on and an airport code (IATA) to be saved; without them, a banner explains what is missing. If Community refuses the connection because the server's clock is wrong, the banner says so: set the server's time right, then choose **Retry**.

## The control journal

The **control journal** gathers traffic that is not a conversation: requests, responses, anonymous requests (such as logins) and group data. Packets are grouped by the node they talk to. When the content stays encrypted, you see the envelope, not the contents.

## RF locate

**RF Locate** draws a conservative coverage zone for a node, from the radios that heard it directly (at zero hops). Type a name, a full key or the start of a key. If several nodes match, you pick one. It is not a GPS point, and nothing is written onto the contact.

It starts from what your own radio heard. With Meshloom Community on, it also uses the community's observers.

## Discovered channels

Meshloom tries to recognize hashtag channels it hears but has no key for. It first tries a built-in list of well-known names. With Community on and an airport code saved, it also asks Community for names that could match. When it finds one, it sends a "Found channels" notification (see [Push notifications](/en/docs/deep/push/)).

A channel found this way does not enter your conversations by itself. It appears in **Discovered channels**, where you choose **Adopt** to add it to your chats or **Refuse** to dismiss it. Refused channels are kept in a list, so you can reverse your decision.

From the same page, **Show Channel Finder** opens the channel finder. It tries names on the unreadable packets: a dictionary of known names, then word pairs, then every combination up to a length you set. The last step uses your computer's graphics card (WebGPU), so it needs Chrome or Edge 113 or later, and a page served over HTTPS or from `localhost` (see [HTTPS](/en/docs/deep/https/)). The channel finder works with Community off.

## Radio test

**Radio test** sends one test message across the network and lists the Community radios that heard it: how many, how far, and how many hops. It is only available when Meshloom Community is on. The message is sent into one of the regions saved on your radio, so you must save at least one in the radio settings first.

## It takes space

Keeping raw packets has a cost. They accumulate and the database grows. The **Database** section of Settings shows its size and the age of the oldest unreadable packet. It offers two clean-ups:

- **Delete Undecrypted Packets** removes the unreadable packets older than a number of days you choose. Deleting them closes the door on decrypting that period later: messages already decrypted remain, but the other packets are gone for good.
- **Purge Archival Packets** removes the raw packets behind messages that are already decrypted. It saves space and does not touch your messages or future decryption, but you can no longer inspect those packets.

On a machine without tight disk limits, leaving everything for a few weeks is usually fine.

Before leaving it running, read [A trusted network](/en/docs/trust/).
