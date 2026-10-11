---
title: Messages
description: Direct messages, channels, hashtag channels, and what happens after you send.
level: start
order: 4
---

There are two ways to write on the mesh, and they behave quite differently.

## Direct messages

A direct message goes to one contact. It is encrypted for that person: the nodes that relay it can carry it but cannot read it.

You need the contact's **public key**, a long string of hexadecimal characters (64 of them) that identifies a node on the network. Usually you do not type anything: when Meshloom hears a node's advert, the contact is created and appears in the conversation list. Otherwise, **Add Channel/Contact** lets you paste a public key by hand (tab **Contact**).

After you send, the message shows how its delivery is going:

- A **receipt** (an acknowledgement) means the recipient confirmed it received the message. Without one, the message was sent, but nothing proves it arrived.
- A direct message with no receipt is retried automatically, up to three attempts in all. Before the last attempt, Meshloom forgets the route it had learned, so the message goes out as a *flood*: it spreads from repeater to repeater until it finds the recipient.
- Click the hop count or the path next to a message to see which repeaters it went through.

A receipt takes time. The message has to cross every repeater on the way, and the confirmation has to come back. Over several hops, a few tens of seconds is normal.

Meshloom decrypts incoming direct messages on the server, with the private key it reads from the radio when it connects. This works even when the contact is no longer loaded in the radio's memory. It needs a radio firmware that allows reading the key.

## Channels

A channel is a shared space (MeshCore calls it a room or a group, Meshloom says **channel**). Everyone who has the same **channel key** can read and write in it. There is no member list, no invitation and no moderation: the key is the access.

A **Public** channel exists from the start. It is MeshCore's default channel, and its key is known to everyone, so treat it like a public square.

To join a private channel, get its key from someone who already has it. Open **Add Channel/Contact**, choose the **Private Channel** tab, then enter the name and the key. The **Generate random key** button creates a new key if you are starting your own channel.

Channel messages often come back several times: each repeater that relays one can bring it back within range of your radio. Meshloom does not display these copies as duplicates. It counts them as **echoes** next to your message and records each route. This is useful: many echoes mean your message travelled well.

Right after you send a channel message, a **Resend** button stays available for 30 seconds, in case it clearly reached nobody. Resend repeats the exact same message, so only repeaters that have not seen it yet pass it on. **Resend as new** sends it as a new message, which receivers may see as a duplicate. If you would rather not do this by hand, **Auto-Resend Unheard Channel Messages** in **Settings > Radio** (off by default) resends a channel message once if no echo comes back within 2 seconds.

A message holds at most 156 bytes, which is about 156 characters without accents (an accented character takes two). On a channel, your node's name is sent with each message and takes part of that space. Sending is blocked when your text is too long, and the composer says "too long". Meshloom does not cut a message for you.

## Replying, reacting and sharing

In a conversation you can reply to a message, react to it with an emoji, copy it, and open its details. The composer can also insert an emoji, a GIF (searching for GIFs needs a Giphy key in **Settings > Local Configuration**; pasting a link works without one) and your radio's position as a location pin. **Delete** removes a message from this Meshloom only. Other nodes keep their copy.

## Hashtag channels

Remembering and typing a 32-character key for each conversation is a chore. Hashtag channels remove it: the key is **calculated from the name**.

The name, with its leading `#`, goes through a hash function (SHA-256, a recipe that always gives the same result for the same input), and the first 16 bytes of the result are the key. Two people who type `#meteo` get the same key and end up in the same channel without exchanging anything. They only need to agree on the name.

Some consequences:

- **The name is hashed exactly as written.** One extra capital letter, space or accent gives a different key, so a different channel. By default Meshloom prevents these near misses, as the official MeshCore app does: it trims spaces at both ends, writes the name in lower case, and accepts only unaccented letters, digits and hyphens. The option **Permit capitals, whitespace, and extended characters** lets you join a channel created elsewhere with an unusual name: the name is then hashed as typed, except for spaces at both ends. Not every app removes those outer spaces, so avoid them in a channel name.
- **The name must be short.** 30 bytes at most including the `#` by default (the same as the official app), and 32 with the option (the radio's limit). An accented character can take more than one byte.
- **A name that is easy to guess is an open channel.** Anyone can type `#meteo`. A hashtag channel organizes conversations, it does not hide them.

You can also add many at once: the **Bulk Add Channel** tab takes a list of names, separated by lines, spaces or commas. Meshloom also finds some hashtag channels by itself: they wait in **Discovered channels**, where you **Adopt** or **Refuse** each one.

## Adding a key later

Meshloom stores the raw packets it hears, including those it could not decrypt. When you add a channel, you can therefore ask it to try decrypting history: the **Try decrypting N stored packets** option makes the server go through the stored packets and recover those the new key can read. Last week's conversation then appears, as long as the packets have not been purged (see **Settings > Database**).

Next: [Around the messages](/en/docs/around/).
