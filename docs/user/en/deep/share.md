---
title: Short links
description: Shorten long web addresses through the Meshloom sharing service: consent, what leaves your machine, modes, bans and limits.
level: deep
order: 23
---

A MeshCore message holds 156 bytes, and a web address often takes more than that. The **sharing service** turns a long address into a short link such as `https://meshl.ink/Ab3dE9x` (25 bytes), which fits in a message and which anybody can open. It is run by the Meshloom project on its own domain, `meshl.ink`, and it is **optional and off by default**: until an administrator turns it on, this Meshloom never contacts it, not even to look up its name.

This version shortens **web addresses** and sends **photos** from the composer's **Send a photo** panel. A public photo can be opened by anyone who has its link. In a private channel or a direct message the panel can also send the photo encrypted for that conversation, which the conversation's members open in Meshloom; on the Public channel, a channel whose key comes from its name (with or without `#`, even renamed locally without its `#`) and a room server, photos are always public. Meshloom cannot tell that a channel was renamed to another letter case or to an unrelated name: its key may still be public. The sections below only describe what exists.

## Turning it on

Open **Settings > Share: links and files** (administrators; a member sees only **My uploads**, see below). The section shows whether sharing is on, whether this Meshloom's sharing identity is ready, and whether encrypted files for direct messages are available.

Ticking **Turn on link and file sharing** always opens a consent window. It lists what leaves this Meshloom, who runs the service and for how long things are kept, links to the service's terms of use and privacy policy, and asks you to tick that you accept the terms. Cancelling, closing the window or leaving the box unticked changes nothing. Your acceptance is recorded on this Meshloom, with the version of the terms and the date: if the terms change in a later version, sharing stays off and the section tells you, until an administrator reads and accepts the new terms by turning it on again. Turning sharing off needs no consent and takes effect at once: requests already on their way are stopped.

If the service is not answering, its terms cannot be read, so sharing cannot be turned on until it answers again (turning it off always stays possible).

## What leaves your machine

Only this server contacts the service, never your browser, and always over HTTPS to `meshl.ink` alone: Meshloom refuses any redirect to another host. It never sends your channel keys or your radio's key.

- **When you shorten a link** (a message you write, or the API): the address you want to shorten, the **public** half of this Meshloom's sharing key inside a signed token that is valid for under two minutes and for that one request, and this Meshloom's IP address, which the service sees on every request.
- **When you send a photo:** the image, shrunk in your browser and without its position and camera data, its size and fingerprint, the same kind of signed token, and this Meshloom's IP address. The photo is public: anyone with its link can see it.
- **When you read:** nothing when a message is displayed, except a shared photo, which this server downloads when the preview setting below lets it load or when you tap it (once: it is then kept in a local cache). When you hover over or focus a short link, Meshloom asks the service what it is (its kind, its destination's host and its expiry) to show the hint described below. These requests carry the object's id and this Meshloom's IP address, and no token.

The service itself keeps short links for 365 days and files for 30 days. Its terms of use and privacy policy, linked from the consent window and published on the service's own domain, say what it keeps and for whom.

Meshloom never uses the radio's key for this. See [The sharing key](/en/docs/deep/security/#the-sharing-key): a random key of its own, created the first time you shorten a link, which the service only sees as a pseudonym. Backups never carry the consent, its recorded acceptance of the terms, nor this key (see [Back up your radio](/en/docs/backup/)).

## Shortening in the composer

**Settings > Share: links and files > Shorten links when you write a message** has three modes:

- **Automatic** (the default): when a message is too long because of a long address, a **Shorten** button appears next to the byte counter. It replaces each long address of the text box with a short link, says how many bytes it saved and offers **Undo** (Undo only works while you have not edited the text since). Nothing is sent by this step: you read the result, then press Send.
- **Always**: pressing Send shortens the long addresses first, then checks the size, then sends. If the message is still too long, it stays in the text box and nothing is sent.
- **Never**: addresses are sent as you typed them, and no button appears.

Shortening is all or nothing: if the service refuses one address (see below), your text is left untouched. Meshloom leaves alone the short links themselves, addresses already short enough, and the `g:`, `m:` and `r:` messages (GIFs, positions and other special messages). It does not shorten a message that is not sent from the composer, such as an extension's. With sharing off, no button appears and nothing is requested, whatever the mode.

Meshloom checks the address before sending it and **never sends** one the service would refuse anyway: an address with a user name or password, an IP address, `localhost`, a name without a dot or ending in `.local`, `.lan`, `.internal` or `.home.arpa`, anything that is not `http` or `https`, or longer than 2048 characters. Such an address is skipped by the button and refused by the API (`share_invalid_url`), so a link to your home network or one that contains credentials never leaves it.

**Settings > Share: links and files > Previews of shared images and links** decides when a shared image in a message loads by itself: always, in private conversations only (direct messages and channels with their own secret key; not Public, not channels whose key comes from their name), or never. Otherwise the image shows a **Tap to load** button, and nothing is requested before you tap. Images from blocked senders, and on the display screen, always wait for a tap. Link previews are not fetched yet.

## Reading short links

A short link in a message looks like any other link, and is an ordinary `https://` address to other MeshCore apps: they open it in a browser, with no Meshloom involved. In Meshloom, hovering over it or giving it focus shows a small hint, `→ example.org`, with the host it leads to, **without following the link**. The hint is asked once per link and remembered for ten minutes (thirty seconds if the service could not be reached), and the host is cleaned of control characters; an international name is shown in its ASCII form.

The service also has, for each link, a **preview page**: add `+` at the end of the address (`https://meshl.ink/Ab3dE9x+`) to see the destination as plain text, never as a link, with its dates and a **Report** button, before deciding to open it. Anyone can use it, in a browser, from any device.

The hint needs sharing to be on: with sharing off, a short link is just a link and nothing is requested.

In push notifications and e-mails a shared image or link never shows its address: it reads `[image]`, `[encrypted file]` or `[link]`, written from the text alone with no request to the service (see [Notifications](/en/docs/deep/notifications/#privacy-and-what-leaves-your-machine)). A script can create links and images through the API: see [Fanout](/en/docs/deep/fanout/#sharing-a-link-or-an-image-from-a-script).

## Opening an encrypted file in a browser

The service also has a page that opens encrypted files outside Meshloom. An encrypted image shared in a **private channel** can be opened by the channel's members, from any device, at `https://meshl.ink/<id>/decrypt` (the file's page links to it): paste the channel's key, its 32 hexadecimal characters or the channel's `meshcore://` link. The browser decrypts the file: the key is not sent to the service, put in the address or saved, and the only request is the download of the encrypted file. A wrong key shows an error and nothing else.

Files shared in a **direct message** cannot be opened there: their key never leaves the Meshloom radio base. Open them in Meshloom.

The page decrypts with code that `meshl.ink` sends each time it is opened, so using it means trusting the code served on that visit. If you do not, open the file in Meshloom.

## Who can do what

Turning sharing on or off and setting the modes needs the **administrator** role. Shortening a link or sending a photo needs the **member** role or above. Viewers and kiosks can read the hints (see [Accounts and access](/en/docs/deep/access/) for the roles). The server checks this on every request, whatever the interface shows, and refuses a request that the browser marks as coming from another web site.

## When something goes wrong

Messages and errors you may see, and what they mean:

- **Sharing is turned off on this Meshloom.** An administrator has to turn it on.
- **The share service cannot be reached right now.** The service is down, slow or answering something unexpected. After three failures in a row, Meshloom stops asking for five minutes (then tries once), so this Meshloom does not hammer it. While it is unreachable, sharing cannot be turned on.
- **This Meshloom has reached its share quota**, **This Meshloom has reached its daily share quota**, **Too many shares from this network today**, **The share service has reached its daily limit**, or **Too many share requests**: the service limits what one instance may create. The wording tells which limit was hit: this instance, the network address it shares from, or the whole service for the day. The service never says how much is left. Try again later. Meshloom also limits itself to 60 hints and 20 downloads per minute; past that it refuses locally, without calling the service.
- **The share service refuses links to this address** (`share_target_blocked`): the destination is on the service's blocklist.
- **The share service has banned this Meshloom.** The service can ban an instance for abuse. A ban concerns **this instance**: one consent, one quota and one ban for everyone using it, as for a radio club sharing one Meshloom. While banned, Meshloom stops writing for 12 hours without asking the service (the ban also ends if you change a sharing setting or restart Meshloom); the links already shared keep working, and you can still read links. Settings > Share shows a banner while the ban holds, and the **Shorten** button reports it. If you think it is a mistake, write to ban@meshloom.app. Community is not affected, since the sharing key is not linked to your radio.

## Retention, removal and contact

A short link stays valid for 365 days, then stops working. To delete a link or file you shared, use **Settings > Share: links and files > My uploads** (members and administrators; an object the service blocked cannot be deleted). Deleting an object, or clearing this Meshloom's cache, does not remove a copy a browser already holds: a public image may stay in a viewer's browser cache for up to one hour. This version of Meshloom has no button to report a link: to report one, use the **Report** button of its preview page (add `+` to the address) or the service's report page. For a removal request or any question about your data, use the contacts on the service's legal pages, `https://meshl.ink/legal` and `https://meshl.ink/privacy`, which also name its publisher.

For the technical side (what each request holds, the key and the tokens), see [Security](/en/docs/deep/security/#what-leaves-your-machine).
