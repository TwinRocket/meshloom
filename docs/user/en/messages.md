---
title: Messages
description: Direct messages, channels, hashtag channels, and what happens after sending.
level: start
order: 4
---

There are two ways to write on the mesh, and they behave quite differently.

## Direct messages

A direct message goes to one contact. It is encrypted for that person: the nodes relaying it can carry it but cannot read it.

You need the contact’s **public key** — a hexadecimal string that identifies them on the network. Usually there is nothing to enter: a heard advert creates the contact automatically and it appears in the conversation list. Otherwise, **Add channel/contact** lets you paste a public key by hand.

After sending, the message shows delivery details:

- A **receipt** means the recipient confirmed receiving it. Without one, the message was sent but there is no proof that it arrived.
- A direct message without a receipt is retried automatically, up to three attempts. The final attempt uses *flood*, spreading from repeater to repeater instead of following a learned route.
- The **path** shows which hops the reply used. Click it to see the details.

A receipt can take time: the message must cross the repeaters on the way out, and the confirmation has to come back. Over several hops, a few tens of seconds is not unusual.

Meshloom decrypts incoming direct messages on the server, using the private key the radio provides at startup. This works even when the contact is no longer loaded in the radio’s memory.

## Channels

A channel is a shared space. Meshloom calls rooms **channels**. Everyone with the same **channel key** can read and write there. There is no member list, invitation, or moderation: the key is the access.

A **Public** channel exists from the start. It is MeshCore’s default channel, with a key known to everyone, so treat it like a public square.

To join a private channel, get its key from someone who already has it. Open **Add channel/contact**, choose the channel tab, then enter the name and key.

Channel messages often come back several times: every repeater relaying one brings it back within range of your radio. Meshloom does not display duplicates. It counts them as **echoes** beside the message, and each echo adds its path. This is useful information: many echoes mean the message travelled well through the network.

A channel message you have just sent keeps a resend button for thirty seconds, in case it clearly reached nobody.

Send is blocked when the draft would overflow the radio packet. Meshloom no longer truncates an over-limit message on send.

## Hashtag channels

Remembering a 32-character hexadecimal key for a conversation is inconvenient. Hashtag channels solve that: the key is **calculated from the name**.

The channel name is hashed — passed through a function that always produces the same key for the same input — and the leading `#` is part of what gets hashed. Two people who type `#meteo` get the same key and end up in the same channel without exchanging anything. They only need to agree on the name.

A few consequences:

- **The name is hashed exactly as written.** An extra capital letter, space, or accent creates a different key and therefore a different channel. By default, Meshloom lowercases the name and limits it to letters, numbers, and hyphens to avoid these near-misses. An option allows capitals, spaces, and extended characters when joining a channel created elsewhere with an unusual name.
- **The name must be short.** Thirty-two bytes maximum, including `#`. Accented characters can use more than one byte.
- **An easy-to-guess name is an open channel.** `#meteo` is not a secret. A hashtag channel helps organize conversations; it does not hide them.

Bulk addition is available too: paste several names at once to create their channels.

## Add a key later

Meshloom keeps the raw packets it hears, including packets it could not decrypt. When you add a channel, an option can therefore **decrypt the history**: the server revisits stored packets and recovers messages that the new key can now read. Last week’s traffic then appears in the conversation, as long as those packets have not been purged.

Next: [Around the messages](/en/docs/around/).
