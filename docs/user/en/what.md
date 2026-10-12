---
title: What it is
description: MeshCore, the radio, Meshloom: what each one does, without the jargon.
level: start
order: 1
---

Three different things have similar names. It helps to separate them first.

## MeshCore, the network

MeshCore is a message network that travels by radio, with no phone operator, no subscription and no Internet connection. The devices use long-range, low-speed radios: a few kilometres in open country, about 150 characters per message, no photos.

It is a **mesh** network: every device can relay what it hears. A message leaves your radio, a repeater on high ground picks it up, another repeater farther away passes it on, and it reaches someone your radio cannot reach directly. Each device is a **node**, and each relay along the way is a **hop**.

There are two ways to write to someone:

- A **direct message**, encrypted for one recipient.
- A **room**, called a **channel** in Meshloom: a shared space where everyone with the same **key** (a secret string used to encrypt and decrypt) can read and write.

Nodes also announce themselves from time to time with an **advert**: a small packet saying "I am here", with a name, a public key and sometimes coordinates. This is how contacts appear without anyone typing them in.

## The companion radio, the box on the desk

A MeshCore companion radio is the physical device: a small box with an antenna, sometimes a screen, usually powered by USB or a battery. It does the radio work and little else. It has neither a comfortable keyboard nor a screen good enough to read a conversation.

It therefore needs a client: a phone, a computer or a server. The client shows messages, chooses who to write to and changes settings. The radio transmits, listens and relays.

Its memory is small. It holds a limited number of contacts and channels, and when it is full, something has to make room.

## Meshloom, the server and its interface

Meshloom is a server installed on a Linux machine, plus a web interface. It connects to the radio over USB, over the network (TCP) or over Bluetooth, and shows everything in a browser.

Two practical consequences:

- **It keeps listening when you close the tab.** The server stays connected to the radio and records every message and packet it hears in a database. Open the page tomorrow and the history is still there.
- **It remembers more than the radio can.** The contacts and channels that do not fit in the radio stay on the server, along with the raw packets. If you add a channel key next week, last week's traffic can be decrypted, as long as those packets are still stored.

Meshloom also adds what a radio alone cannot do: a map of the nodes it has heard, a visualizer of the paths packets actually took, a feed of raw packets, a search across all your messages, and outputs to MQTT, Home Assistant, a webhook, Apprise (Discord, Telegram, email and more) or an SQS queue. Repeaters can be polled at regular intervals, with an alert when a reading leaves the limits you set. Notifications can reach you by Web Push, email or webhook. Meshloom can also share your radio over the network, so a phone app can use it through Meshloom.

**Meshloom Community** is an optional shared directory. It gives names to nodes you have not heard yet, shows which other radios heard the same packet, and offers a transmission test. It also helps decrypt public hashtag channels you do not know yet; they then appear in **Discovered channels**. Meshloom already carries a list of common public channel names and tries them on its own, even without Community.

## What Meshloom is not

It is not firmware: the radio keeps its own, and Meshloom does not replace it. The server runs on your machine. A **new install** joins [Meshloom Community](/en/docs/deep/community/) unless you opt out, but nothing is published until you enter the code of your nearest airport. After that, it can publish the raw packets it overhears to the Meshloom Community servers. Existing databases stay as they were.

One thing to know before you start: **Meshloom takes control of the radio's contacts and channels.** It loads, unloads and replaces them according to what it thinks is useful. That is how it works around the device's memory limit. It is a poor fit if you often swap radios and expect each device to keep its own favorites.

As for its origin: Meshloom started from the MeshCore web client written by Jack Kingsman, which Ian Langworth carried on for a while. The original copyright remains in the MIT licence.

Next: [Install](/en/docs/install/).
