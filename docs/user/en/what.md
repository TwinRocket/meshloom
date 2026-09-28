---
title: What it is
description: MeshCore, the radio, Meshloom — what each one does, without the jargon.
level: start
order: 1
---

Three different things have similar names. It helps to separate them first.

## MeshCore, the network

MeshCore is a message network that travels by radio, with no carrier, subscription, or internet connection. Devices use long-range, low-bandwidth radios: a few kilometres in open country, a few hundred bytes per message, no photos.

It is a **mesh** network: every device can relay what it hears. A message leaves your radio, a repeater on high ground picks it up, another repeater farther away passes it on, and it reaches someone your radio cannot reach directly. Each device is a **node**, and each relay along the way is a **hop**.

There are two ways to write to someone:

- A **direct message**, encrypted for one recipient.
- A **room**, called a channel in Meshloom: a shared space where everyone with the same **key** — a string used to encrypt and decrypt — can read and write.

Nodes also announce themselves at regular intervals with an **advert**: a small packet saying “I am here”, along with a name, public key, and sometimes coordinates. That is how contacts appear without anyone entering them by hand.

## The companion radio, the box on the desk

A MeshCore companion radio is the physical device: a small box with an antenna, sometimes a screen, usually powered by USB or a battery. It handles the radio work and little else. It has neither a comfortable keyboard nor a useful screen for reading a conversation.

It therefore needs a client: a phone, computer, or server. The client displays messages, chooses who to write to, and changes settings. The radio transmits, listens, and relays.

Its memory is small. It holds a few hundred contacts and a handful of rooms, no more. Once it is full, something has to make room.

## Meshloom, the server and interface

Meshloom is a server installed on a Linux machine, plus a web interface. It connects to the radio over USB, TCP, or Bluetooth, and puts everything in a browser.

Two practical consequences:

- **It keeps listening when the tab is closed.** The server stays connected to the radio and writes every message and packet it hears to a database. Open the page tomorrow and the history is still there.
- **It keeps more than the radio can.** Contacts and rooms that do not fit in the radio remain on the server, along with raw packets. Add a room key next week and last week’s traffic can be decrypted, as long as those packets are still stored.

Meshloom also adds what a radio alone cannot: a map of heard nodes, a visualizer of the paths packets actually took, a raw packet feed, and outputs to MQTT, Home Assistant, a webhook, Apprise, or an SQS queue. Meshloom Community adds the shared directory — names for nodes you have not heard yet, radios that heard the same packet, a transmission test — and decryption of public channels you do not know yet, which then show up in Discovered channels. Repeaters can be polled at a regular interval, with an alert when a reading leaves the limits you set.

## What Meshloom is not

It is not firmware: the radio keeps its own, and Meshloom does not replace it. The server runs on your machine. A **new install** joins [Meshloom Community](/en/docs/deep/community/) unless you opt out — that can publish overheard raw packets to the official Stats hosts. Existing databases stay as they were.

One thing to know before starting: **Meshloom takes control of the radio’s contacts and rooms.** It loads, unloads, and replaces them according to what it considers useful. That is how it gets past the device’s memory limit. It is a poor fit if you often switch radios and expect each device to keep its own favourites.

As for its origin: Meshloom started from the MeshCore web client written by Jack Kingsman, which Ian Langworth carried on for a while. The original copyright remains in the MIT licence.

Next: [Install](/en/docs/install/).
