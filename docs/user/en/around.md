---
title: Around the messages
description: The map, the visualizer, the packet feed, the feed — what you watch without sending anything.
level: start
order: 5
---

A radio hears much more than what is addressed to you. Adverts (a node announcing itself), echoes, messages from other channels, unreadable packets: everything within range passes through the antenna. Meshloom keeps this material and gives you a few ways to look at it.

None of this is needed to send a message. It is observation. You can skip it entirely.

On a phone, open these tools from **Tools** in the bottom bar. Each one comes with a one-line description. On a large screen, they sit on the icon rail on the left, and you can reorder or remove them in **Settings → Navigation**. The node map is its own destination, next to your conversations.

### Start page

**Settings → Navigation → Start page** chooses where Meshloom opens when the address has no `#` part. The default is your conversations, as before. Pick the map or any tool (the Feed, its Control preset, the live view…) to open there instead. It is a setting of the instance, changed by an administrator, and it applies to every session and browser, including a kiosk screen. A link to a conversation or a tool still wins, and so does **Reopen the last viewed** on a device where you turned it on and that has a view to reopen. A tool your role may not use is skipped and you land on your conversations.

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

**Live** draws packets on a map as they are heard. It shows packets heard by Meshloom Community observers as well as by your own radio, so you see activity far beyond your own range. You can choose the regions shown, hide packet types, keep only the traces whose route is certain, and choose a sound theme.

**Regions** opens a list of airport codes (IATA) with a box to tick for each. By default only your own region is ticked, the code saved in Meshloom Community. Tick other codes to add them, or **All regions** to see the whole feed. Unticking the last code also shows every region. The list offers the regions active in the last 24 hours, the ones already seen on the map and your own. The filter follows the packet: when a packet is heard in a region you chose, all its observations are drawn, even those heard elsewhere. Packets heard by your own radio always appear. Your choice is remembered in this browser.

When the page opens, and each time you change the regions, the map frames the nodes around the airports of the chosen regions (within about 150 km), or the airport itself if no node is known there yet. With **All regions** it frames every known node. Once you move the map yourself, it stays where you put it until you change the regions again.

Your own radio's packets always appear. The packets of Community observers need Meshloom Community to be on and an airport code (IATA) to be saved; without them, a banner explains what is missing. If Community refuses the connection because the server's clock is wrong, the banner says so: set the server's time right, then choose **Retry**.

Meshloom Community can pause its public feed for a while. A small **Live paused** label then appears next to the sound setting, and only your own radio's packets are drawn. There is nothing to do: your server checks again every minute or so and the Community packets come back on their own when the feed resumes.

If Meshloom Community has banned your radio, the banner says so and the Community packets stop; see [If your radio is banned](/en/docs/deep/community/#if-your-radio-is-banned).

## The feed

The **Feed** puts everything in one timeline, newest at the bottom: the messages of all your channels and direct messages, and the control traffic (requests, responses, anonymous requests such as logins, and group data) between them, in time order. When the content of a control packet stays encrypted, you see the envelope, not the contents; click a row to open the packet inspector.

- **Chips.** *Messages*, *Control* and *Events* choose the sources (all three are on by default). Under *Messages*, *Channels*, *Direct messages*, *Received* and *Sent* narrow what is shown, and *Muted channels* and *Pending channels* bring back the ones hidden by default. The **Direct messages** chip is a display choice only: hiding them does not protect them, since anyone who can open Meshloom can read them (see [A trusted network](/en/docs/trust/)). Your choice is remembered in this browser. An account that may not read direct messages (the `kiosk` role) has no **Direct messages** chip at all: the Feed asks the server for channels only, an address with `types=priv` is ignored, and a remembered choice for direct messages is left in storage but not used. Control, and muted or pending channels, stay readable. If the server ever refuses the list, the Feed says so in its empty state instead of showing an error.
- **Channels and Contacts.** A column on the right (on a phone, the **Filters** button opens it as a sheet, with the number of picked conversations: **Filters (2)**) lists the channels and the contacts present in the rows the Feed has loaded, with how many messages each holds. The counts are **for the loaded rows** only, not for the whole history: they follow the other chips and grow as you scroll up and older pages load. Tick one or more conversations to narrow the timeline to them; **Reset** clears the pick. Control and event rows are not conversations and stay. Your pick is remembered in this browser, is not part of the address, and is ignored in the display mode.
- **Quick filter and search.** The field at the top of that column filters the rows the Feed has **already loaded**: it keeps the messages whose text, sender or conversation name contains what you typed (case-insensitive), shows how many match, and hides control and event rows meanwhile. Technical payloads (reactions, GIFs, locations) are not matched by their coded text. It never asks the server, so older messages that are not loaded are not searched: **Search the whole history** opens message search with the same text. Messages that mention your radio as `@[name]` get an accent bar on the left; messages you sent never do. The field is not remembered and is not in the address.
- **Nodes.** With *Control* on, a **Nodes** facet lists the repeaters and rooms, the channels, the packets addressed to you and the unresolved ones. Picking one narrows the control rows to it.
- **Events.** With *Events* on, the feed adds one line when something changes around the radio: **the radio is offline** (after it stayed unreachable for the *radio offline* delay of the Notifications rules: when a notification channel receives that event, the server tells every open page as the delay ends; otherwise administrators' sessions use that delay and other sessions the default of one minute; a short flap and the reboots Meshloom starts itself add nothing) and **back online** with how long it was away, **a new contact heard** (not unknown or room contacts, and not one you add by hand in this tab), **a contact renamed**, and **a channel discovered**. A line offers what you may do with it: open the contact, review the channel in *Discovered channels* (accounts that can send), open the radio settings (administrators). Events are kept for this session only: closing or reloading the page forgets them, and an event that happened while the Feed was never opened is not recovered, and a radio already offline when the page loads is not reported. They use the same names as the notifications of *Settings > Notifications*: the events a notification channel receives come from the server as they happen (the **Feed events** option of the rules, on by default), the others, or all of them when that option is off, are worked out by the page itself, with the same lines. Each page only gets what its account may read, and in a node picked in the Nodes facet, only that node's events remain. In the display mode they are shown without any action.
- **Earlier control packets.** Control rows are live: after a reload the Feed would start without any. So, once the first page of messages is in, it also asks the server for the 200 most recent control packets it stored (scanning at most the 20,000 newest stored packets) and adds them, marked **Earlier**. They come from the stored packets, so they have no signal strength (RSSI/SNR) and appear once per packet, even if several repeaters relayed it. What it can show is limited by what was stored: if you enabled the automatic deletion of unreadable packets (`raw_packet_retention_days`), the requests and anonymous requests between other nodes, which Meshloom cannot decrypt, are deleted after that delay. A line above the list offers **Load earlier control packets** to go further back; when the server stopped at its scan limit it says so (older control packets may exist, and the button continues from there). The Feed keeps at most 500 control rows.
- **Follow mode.** The feed stays at the newest row while you are at the bottom. Scroll up and a **Follow** button counts what arrived since and takes you back. Scrolling up also loads older messages.
- **Since your last visit.** A line **New since your last visit** marks the first message that arrived after you last had the feed open, in this browser.
- **Reading marks nothing as read.** Unread counts stay as they are until you open the conversation itself.

The filters are also in the address bar. Change a chip and the address becomes, for example, `#feed?src=messages,control&types=chan,priv&dir=in,out&muted=0&pending=0`: bookmark it, or use it as the kiosk screen's address, and the Feed opens with those filters whatever the browser remembers (the address wins over the remembered filters, which win over the defaults). `src` lists the sources (`messages`, `control`, `events`), `types` the kinds (`chan`, `priv`), `dir` the directions (`in`, `out`), `muted` and `pending` are `1` to show those channels, `node` is a node's public key (64 hexadecimal characters), and `display=1` turns on the display mode (below). **Channels are not in it on purpose**: a private channel's key is its access, and bookmarks are synced by your browser's account, so a channel key is never written in an address. Anything unknown or malformed in the address is ignored. The same form (`feed?...`, `control?...`, without the `#`) can be the **Start page**.

### Display mode

The **Display mode** button in the Feed's header (or `display=1` in the address) turns the Feed into a screen for the wall: text 25 % larger, no rail, menu, header or filter chips, the newest message always in view, day separators and a small clock, no blur or shadow, and full screen when the browser allows it. **Exit** (top right) brings back your filters exactly as they were; your remembered filters and your choice about reactions are never changed. Direct messages are hidden and images and GIFs are not loaded, whatever the address says; **Exit** is the only control. Hiding direct messages is a display choice, not an access control: the browser still receives them, and what keeps them off a shared screen is the `kiosk` account. See [Raspberry Pi image](/en/docs/rpi/#display-mode).

### The Control preset

**Control** opens the same feed with only the *Control* source on, with the Nodes list beside it on a computer or already open on a phone (once nodes are known): the old control journal. It is a preset, not a separate page: the address `#control` and bookmarks to it keep working, and the address bar still shows `#control`. Your changes to the chips there are not remembered, so the regular feed keeps the filters you chose for it. **Control** is not in the icon bar by default; add it in **Settings > Navigation**, or find it in the search palette (Ctrl/Cmd+K).

## RF locate

**RF Locate** draws a conservative coverage zone for a node, from the radios that heard it directly (at zero hops). Type a name, a full key or the start of a key. If several nodes match, you pick one. It is not a GPS point, and nothing is written onto the contact.

It starts from what your own radio heard. With Meshloom Community on, it also uses the community's observers.

## Discovered channels

Meshloom tries to recognize hashtag channels it hears but has no key for. It first tries a built-in list of well-known names. With Community on and an airport code saved, it also asks Community for names that could match. When it finds one, it sends a "Found channels" notification (see [Notifications](/en/docs/deep/notifications/)).

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
