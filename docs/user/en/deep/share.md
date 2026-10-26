---
title: Short links and photos
description: Shorten web addresses and share photos, public or encrypted, through the Meshloom sharing service: consent, what leaves your machine, the limits of encryption, bans and quotas.
level: deep
order: 23
---

A MeshCore message holds 156 bytes, and a web address often takes more than that. The **sharing service** turns a long address into a short link such as `https://meshl.ink/Ab3dE9x` (25 bytes), which fits in a message and which anybody can open. It is run by the Meshloom project on its own domain, `meshl.ink`, and it is **optional and off by default**: until an administrator turns it on, this Meshloom never contacts it, not even to look up its name.

This version shortens **web addresses** and sends **photos** from the composer's **Send a photo** panel. A photo is either **public**, which anyone who has its link can open, or, in a private channel or a direct message, **encrypted** for that conversation, which its members open in Meshloom. Encryption is offered only where it protects something, and it has honest limits: read [Encrypted files](#encrypted-files) before relying on it. Meshloom cannot tell that a channel was renamed to another letter case or to an unrelated name: its key may still be public. The sections below only describe what exists.

## Turning it on

Open **Settings > Share: links and files** (administrators; a member sees only **My uploads**, see below). The section shows whether sharing is on, whether this Meshloom's sharing identity is ready, and whether encrypted files for direct messages are available.

Ticking **Turn on link and file sharing** always opens a consent window. It lists what leaves this Meshloom, who runs the service and for how long things are kept, links to the service's terms of use and privacy policy, and asks you to tick that you accept the terms. Cancelling, closing the window or leaving the box unticked changes nothing. Your acceptance is recorded on this Meshloom, with the version of the terms and the date: if the terms change in a later version, sharing stays off and the section tells you, until an administrator reads and accepts the new terms by turning it on again. Turning sharing off needs no consent and takes effect at once: requests already on their way are stopped.

If the service is not answering, its terms cannot be read, so sharing cannot be turned on until it answers again (turning it off always stays possible).

## What leaves your machine

Only this server contacts the service, never your browser, and always over HTTPS to `meshl.ink` alone: Meshloom refuses any redirect to another host. It never sends your channel keys or your radio's key.

- **When you shorten a link** (a message you write, or the API): the address you want to shorten, the **public** half of this Meshloom's sharing key inside a signed token that is valid for under two minutes and for that one request, and this Meshloom's IP address, which the service sees on every request.
- **When you send a photo:** the image, shrunk in your browser and without its position and camera data, its size and fingerprint, the same kind of signed token, and this Meshloom's IP address. A public photo is sent as it is, and anyone with its link can see it. An encrypted photo is sealed by this server before it leaves, with a key that stays here (see [Encrypted files](#encrypted-files)): the service gets unreadable bytes.
- **When you read:** nothing when a message is displayed, except a shared photo, which this server downloads when the preview setting below lets it load or when you tap it (once: it is then kept in a local cache). When you hover over or focus a short link, Meshloom asks the service what it is (its kind, its destination's host and its expiry) to show the hint described below. These requests carry the object's id and this Meshloom's IP address, and no token.

The service itself keeps short links for 365 days and files, public or encrypted, for 30 days. Its terms of use and privacy policy, linked from the consent window and published on the service's own domain, say what it keeps and for whom.

Meshloom never uses the radio's key for this. See [The sharing key](/en/docs/deep/security/#the-sharing-key): a random key of its own, created the first time you shorten a link, which the service only sees as a pseudonym. Backups never carry the consent, its recorded acceptance of the terms, nor this key (see [Back up your radio](/en/docs/backup/)).

## Shortening in the composer

**Settings > Share: links and files > Shorten links when you write a message** has three modes:

- **Automatic** (the default): when a message is too long because of a long address, a **Shorten** button appears next to the byte counter. It replaces each long address of the text box with a short link, says how many bytes it saved and offers **Undo** (Undo only works while you have not edited the text since). Nothing is sent by this step: you read the result, then press Send.
- **Always**: pressing Send shortens the long addresses first, then checks the size, then sends. If the message is still too long, it stays in the text box and nothing is sent.
- **Never**: addresses are sent as you typed them, and no button appears.

Shortening is all or nothing: if the service refuses one address (see below), your text is left untouched. Meshloom leaves alone the short links themselves, addresses already short enough, and the `g:`, `m:` and `r:` messages (GIFs, positions and other special messages). It does not shorten a message that is not sent from the composer, such as an extension's. With sharing off, no button appears and nothing is requested, whatever the mode.

Meshloom checks the address before sending it and **never sends** one the service would refuse anyway: an address with a user name or password, an IP address, `localhost`, a name without a dot or ending in `.local`, `.lan`, `.internal` or `.home.arpa`, anything that is not `http` or `https`, or longer than 2048 characters. Such an address is skipped by the button and refused by the API (`share_invalid_url`), so a link to your home network or one that contains credentials never leaves it.

## Sending a photo

Open the attach button next to the text box, choose **Send a photo**, then drop a photo on the panel, paste one into the message box or choose a file. Add a caption if you like and press **Send photo**. The message sent is `📷` and the link (`🔒` and the link for an encrypted photo) followed by the caption. It is an ordinary MeshCore message of 156 bytes at most: the panel counts the bytes and asks you to shorten a caption that does not fit. Sending needs the **member** role or above.

Before anything leaves your browser, Meshloom prepares the photo:

- **It is scaled down.** The longest side becomes 1600 pixels at most (a smaller photo is never enlarged) and the photo is saved again, as JPEG by default. That new copy has no position, camera data or shooting time, and the panel shows the size before and after. A PNG with transparency stays PNG, and a WebP stays WebP if your browser can write one. A **GIF is sent as it is**, because re-encoding would flatten the animation: it is neither scaled nor cleaned.
- **Only images are accepted: JPEG, PNG, WebP and GIF.** Meshloom and the service refuse every other type, and check the real content, not the file name. Other formats can run code in a browser or hide a program, which a service whose links anybody opens must not host. iPhone HEIC photos cannot be read by a browser: share them as JPEG (Settings > Camera > Formats > Most Compatible) or pick them again through the photo picker, which converts them.
- **Sizes:** a file of up to 40 MiB can be chosen. Once prepared it must weigh **10 MiB at most**, or **5 MiB for a GIF**, and hold **40 million pixels at most**. Meshloom checks type, size and dimensions again on the server before sending, in its `share-tmp` folder, which it empties right after (see [Other install paths](/en/docs/deep/install-paths/#database-and-updates)). At most two photos are received at a time.
- **The service limits what each Meshloom can send.** By default a Meshloom's first 7 days allow 5 files and 10 MiB a day. After that it is 50 files and 200 MiB a day, and a network address shared by several Meshloom gets twice that. The whole service also has a daily budget. These are the service's defaults and it can change them: Meshloom never shows how much is left (see [When something goes wrong](#when-something-goes-wrong)).

Where the conversation allows it, a **Who can open the photo** choice offers **Public** and **Encrypted** (Encrypted is preselected, then your last choice is remembered). Elsewhere the panel says that the photo is published in clear.

## Reading photos

In Meshloom, a shared photo shows under the text of its message, 240 pixels wide or high at most. Clicking it opens it full size in a new tab. Beyond 8 MiB a **Download the image** card is shown instead. An expired or removed photo says so. It is this server that fetches the photo, never your browser, and it keeps a copy in a local cache for up to one hour (see [Other install paths](/en/docs/deep/install-paths/#database-and-updates)). A photo that does not appear is not an error of yours: the service may be unreachable, or sharing may be off.

**Settings > Share: links and files > Previews of shared images and links** decides when a shared photo in a message loads by itself: always, in private conversations only (direct messages and channels with their own secret key; not Public, not channels whose key comes from their name; this is the default), or never. Otherwise the photo shows a **Tap to load the image** button, and **nothing is requested before you tap**: not the photo, and not even a question about it. Photos from blocked senders, and on the display screen, always wait for a tap. Link previews are not fetched yet.

Outside Meshloom, other MeshCore apps show the address as text. Opened in a browser, the address of a public photo shows a page with the photo, its size, its expiry date and a **Report** link. Add `+` at the end of the address to see only its type, size and dates, without displaying the photo. A search engine is told not to index these pages.

**Report from the card.** Members and administrators have a **Report** button under a shared photo. Choose a reason among eight (illegal content, child sexual abuse, phishing or scam, malware, spam, copyright, privacy violation, other), add details if you want, and the report goes to the service's moderators, signed with this Meshloom's key. A Meshloom can send 5 reports a day. A report does not remove the photo from your screen: to stop seeing a sender, block them.

## Reading short links

A short link in a message looks like any other link, and is an ordinary `https://` address to other MeshCore apps: they open it in a browser, with no Meshloom involved. In Meshloom, hovering over it or giving it focus shows a small hint, `→ example.org`, with the host it leads to, **without following the link**. The hint is asked once per link and remembered for ten minutes (thirty seconds if the service could not be reached), and the host is cleaned of control characters; an international name is shown in its ASCII form.

The service also has, for each link, a **preview page**: add `+` at the end of the address (`https://meshl.ink/Ab3dE9x+`) to see the destination as plain text, never as a link, with its dates and a **Report** button, before deciding to open it. Anyone can use it, in a browser, from any device.

The hint needs sharing to be on: with sharing off, a short link is just a link and nothing is requested.

In push notifications and e-mails a shared image or link never shows its address: it reads `[image]`, `[encrypted file]` or `[link]`, written from the text alone with no request to the service (see [Notifications](/en/docs/deep/notifications/#privacy-and-what-leaves-your-machine)). A script can create links and images through the API: see [Fanout](/en/docs/deep/fanout/#sharing-a-link-or-an-image-from-a-script).

## Encrypted files

An encrypted photo is sealed so that only the people of one conversation can open it. It exists only where the key of the conversation is really secret:

| Conversation | Encrypted photo? | Why |
|---|---|---|
| The Public channel | No, always public | Its key is known to everyone. |
| A channel whose key comes from its name (hashtag, with or without `#`) | No, always public | Anyone who knows or guesses the name computes the key. |
| A private channel (a key that was given to its members) | Yes | Only the people who hold the key can open the photo. |
| A direct message | Yes | The key comes from your radio's key and your contact's. |
| A room server | No, always public | The server reads every post. |

Where encrypted is not offered, the panel says so: the photo is published in clear, and **anyone who has the link can see it**. Nothing sent on the Public channel or in a hashtag channel is private, with or without this feature. Meshloom does not show a choice there because it would only look private.

How it works. Your browser sends the photo to your Meshloom, which seals it on the server with AES-256-GCM and a key derived from the conversation's secret and the file's id, then sends the sealed bytes to the service. The channel key never goes back to your browser (the browser only names the channel by an opaque id) and never goes to the service. The name of your file is sealed inside too.

What the service sees: that a file exists, its exact size (that of the sealed bytes, a little more than the photo), its dates, the pseudonymous sharing key of your Meshloom and a fingerprint of the sealed bytes. It does not see the content, the name, the type, nor who is in the conversation. Like for any request, it also sees this Meshloom's IP address. The page of an encrypted file (its address in a browser) says the same and shows a download of the sealed bytes, which are unreadable without the key.

The limits, plainly:

- **No forward secrecy.** The key of a conversation does not change from file to file or over time. Whoever obtains the key of a private channel later (a person you invite, a member who copied it, a former member who kept it) can read **every encrypted file that is still online**. And anyone who saved the sealed bytes while they were online can read them whenever they get the key, even after the file expired. For a direct message, whoever obtains the private key of either radio is in the same position.
- **Anyone who sees the link can download the sealed file** and see its size and dates, just not its content. The link travels in the message itself, over the air.
- **Meshloom itself reads the files.** Your server holds the keys and decrypts for every account allowed to read that conversation (a `kiosk` account never reads direct messages). The decrypted photo is sent to the browser with a no-store instruction, and the local cache keeps only the sealed bytes. This protects against the service and against people who only have the link, not against someone who controls your Meshloom.
- **Files expire after 30 days**, as public ones. **My uploads** deletes them earlier.
- **A link does not travel.** Meshloom opens an encrypted file only for the conversation it was sent in. Copy the link into another conversation and Meshloom shows "Encrypted for another conversation" and the raw link. Other MeshCore apps cannot open it either: they show a link to the page described above.
- **Direct messages need your radio's key.** Each side opens the file with the key shared between its radio and the contact's. Meshloom reads the radio's key whenever the radio is connected; as long as it has not been read (the radio is disconnected, for example), encrypted direct-message files can neither be sent nor opened ("cannot be read right now"). Public photos, links and the sharing identity do not depend on it. Once the radio is back, the files open again.
- **Reports are handled without looking.** The service cannot open an encrypted file, so a report on one must be specific: the channel or conversation, who shared it and the context (use the **Details** box of the report). On such a report the file is blocked without being viewed. A second, distinct and specific report about a file from the same Meshloom leads to a ban of that Meshloom, that is of everyone who uses it (see below).

## Opening an encrypted file in a browser

The service also has a page that opens encrypted files outside Meshloom. An encrypted image shared in a **private channel** can be opened by the channel's members, from any device, at `https://meshl.ink/<id>/decrypt` (the file's page links to it): paste the channel's key, its 32 hexadecimal characters or the channel's `meshcore://` link. The browser decrypts the file: the key is not sent to the service, put in the address or saved, and the only request is the download of the encrypted file. A wrong key shows an error and nothing else.

Files shared in a **direct message** cannot be opened there: their key never leaves the Meshloom radio base. Open them in Meshloom.

The page decrypts with code that `meshl.ink` sends each time it is opened, so using it means trusting the code served on that visit. If you do not, open the file in Meshloom.

## Who can do what

Turning sharing on or off, setting the modes and clearing this Meshloom's cache need the **administrator** role. Shortening a link, sending a photo, reporting an object and the **My uploads** list need the **member** role or above. Viewers and kiosks can read the hints and see the photos of the conversations they may read (a kiosk never sees direct messages, nor a photo or link preview shared in one; see [Accounts and access](/en/docs/deep/access/) for the roles). The server checks this on every request, whatever the interface shows, and refuses a request that the browser marks as coming from another web site.

On a Meshloom with several accounts, the [audit log](/en/docs/deep/access/#audit-log) (administrators) records who turned sharing on or off, who deleted a shared object and who reported one, with the reason chosen (never the details typed).

A Meshloom has **one** sharing identity, consent, quota and ban, whoever uses it. On a Meshloom shared by several people, every member sees all that this Meshloom shared in **My uploads**, can delete it, and shares the same daily quota. A ban for one person's abuse stops everybody.

## When something goes wrong

Messages and errors you may see, and what they mean:

- **Sharing is turned off on this Meshloom.** An administrator has to turn it on.
- **The share service cannot be reached right now.** The service is down, slow or answering something unexpected. After three failures in a row, Meshloom stops asking for five minutes (then tries once), so this Meshloom does not hammer it. While it is unreachable, sharing cannot be turned on.
- **This Meshloom has reached its share quota**, **This Meshloom has reached its daily share quota**, **Too many shares from this network today**, **The share service has reached its daily limit**, or **Too many share requests**: the service limits what one instance may create. The wording tells which limit was hit: this instance, the network address it shares from, or the whole service for the day. The service never says how much is left. Try again later. Meshloom also limits itself to 60 hints and 20 downloads per minute; past that it refuses locally, without calling the service.
- **The share service refuses links to this address** (`share_target_blocked`): the destination is on the service's blocklist.
- **This file is too large to share** or **This type of file cannot be shared** (`share_too_large`, `share_type_refused`): see the sizes and types in [Sending a photo](#sending-a-photo). The panel also explains HEIC photos, which a browser cannot read.
- **Encrypted files are not possible in this conversation** (`share_mode_not_allowed`): the Public channel, a hashtag channel and a room server only take public photos. See the table in [Encrypted files](#encrypted-files).
- **The radio's key has not been read yet** (`share_radio_key_unavailable`) or **This encrypted file cannot be decrypted in this conversation** (`share_undecryptable`): see the direct message and link limits in [Encrypted files](#encrypted-files).
- **The share service has banned this Meshloom.** The service can ban an instance for abuse. A ban concerns **this instance**: one consent, one quota and one ban for everyone using it, as for a radio club sharing one Meshloom. While banned, Meshloom stops writing for 12 hours without asking the service (the ban also ends if you change a sharing setting or restart Meshloom); the links already shared keep working, and you can still read links. Settings > Share shows a banner while the ban holds, and the **Shorten** button reports it. If you think it is a mistake, write to ban@meshloom.app. Community is not affected, since the sharing key is not linked to your radio.

## Retention, removal and contact

A short link stays valid for 365 days, then stops working. A photo, public or encrypted, stays online for 30 days, then it is removed. To delete a link or file you shared earlier, use **Settings > Share: links and files > My uploads** (members and administrators; it lists what this whole Meshloom shared, with the type, size, expiry and status of each object). An object the service blocked shows its category and cannot be deleted: the page links to the service's contacts. Deleting an object, or clearing this Meshloom's cache, does not remove a copy a browser already holds: a public photo may stay in a viewer's browser cache for up to one hour (a decrypted photo is not kept). Under a shared photo, the **Report** button reports it to the service; for a short link, use the **Report** button of its preview page (add `+` to the address) or the service's report page. For a removal request or any question about your data, use the contacts on the service's legal pages, `https://meshl.ink/legal` and `https://meshl.ink/privacy`, which also name its publisher.

For the technical side (what each request holds, the key and the tokens), see [Security](/en/docs/deep/security/#what-leaves-your-machine).
