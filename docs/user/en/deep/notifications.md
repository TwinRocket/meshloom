---
title: Notifications
description: Channels, events and rules: tell yourself when something happens, on your phone, by e-mail, on ntfy, Telegram, Discord, Home Assistant and more.
level: deep
order: 17
---

Meshloom can tell you about what it hears and about its own health without you looking at the interface: a direct message, a mention, a repeater that goes quiet, the radio that drops off. Each place it sends to is a **channel**. What goes to which channel is decided by one table of **rules**, the administrator's defaults for the whole installation. Each account can then adjust, for its own Web Push devices only, which events reach them: see [Your own rules](#your-own-rules).

Everything is in **Settings → Notifications** (administrators only, except [My notifications](#your-own-rules)). Nothing here needs an environment variable. Browser-specific matters (HTTPS, the VAPID contact address, subscribing a browser) are on [Push notifications](/en/docs/deep/push/); this page covers everything else.

The notification channels are separate from [fanout](/en/docs/deep/fanout/), which forwards every message of the conversations you pick to machines (MQTT, webhooks, SQS). Notifications are for people and tell you about events.

## Channels

A channel is one destination, with its own name, settings, text policy, language and on/off switch. You can have as many as you like, of any type, several of the same type included (two ntfy topics, a Telegram chat for the family and one for the club).

| Type | What it reaches | Message text at the start |
|------|-----------------|---------------------------|
| Web Push | The browsers you subscribed (one built-in channel, it cannot be deleted; needs HTTPS) | Always (no text policy, see [Privacy](#privacy-and-what-leaves-your-machine)) |
| E-mail | Recipients, through your SMTP server | Public channels only, unless the SMTP server is on your network |
| Webhook | A URL you control, with a JSON body and an optional signature | Always |
| ntfy | An [ntfy](https://ntfy.sh) topic on ntfy.sh or on your own server | Public channels only on ntfy.sh, always on your own server |
| Discord | A Discord channel, through a webhook | Public channels only |
| Telegram | A chat, group or channel, through your own bot (preset) | Public channels only |
| Gotify | Your own Gotify server (preset) | Always |
| Home Assistant | A Home Assistant notify service, such as the Companion app (preset) | Always |
| Pushover | The Pushover app on your phone (preset) | Public channels only |
| Matrix | A Matrix room, as a bot or an account (preset) | Public channels only |
| Signal | Signal messages through your own signal-cli REST server (preset) | Always |
| Slack | A Slack channel, through an incoming webhook (preset) | Public channels only |
| Mattermost | A Mattermost channel, through an incoming webhook (preset) | Always |
| Rocket.Chat | A Rocket.Chat room, as an account (preset) | Always |
| SMS Free Mobile | An SMS to your own Free Mobile line (preset) | Never (title only) |
| SMS SFR | SMS to numbers through the SFR messaging service (preset) | Never (title only) |
| Apprise | Any other service [Apprise](https://appriseit.com/) supports, from its URL | Always |

**Native types** (Web Push, e-mail, webhook, ntfy, Discord) are written for the service. **Presets** (Telegram, Gotify, Home Assistant, Pushover, Matrix, Signal, Slack, Mattermost, Rocket.Chat, SMS Free Mobile, SMS SFR) are ready-made forms for services that Meshloom reaches through Apprise: you fill in named fields, Meshloom builds the Apprise address when it sends, and it never stores that address. The generic **Apprise** type is the way out for everything else. A channel's type cannot change after it is created: make a new one.

Everything about a channel, its secrets included, is explained in [Adding a channel](#adding-a-channel) and the sections of each type below.

### Adding a channel

**Settings → Notifications → Channels** (administrators) lists every place Meshloom sends notifications to as a card: its type, a status dot (working, error, paused, off), the last error, an on/off switch, **Test**, **Edit** and **Delete**. The **Web Push** card is always there and cannot be deleted; open **Devices and Web Push settings** on it for this browser's subscription, the other devices, the VAPID subject and the private-endpoint switch. Over plain HTTP the card says Web Push is unavailable.

**Add a channel** opens a dialog in three steps. First pick a type or a preset (Email, Webhook, ntfy, Discord, Telegram, Gotify, Home Assistant, Pushover, Matrix, Signal, Slack, Mattermost, Rocket.Chat, SMS Free Mobile, SMS SFR or Apprise). Then fill its form, which the server describes itself: required fields are starred, the server's validation errors appear under the field, and a secret (a password, a token, a webhook address) is never shown, not even after saving: the form says a value is set and offers **Replace** and **Clear** (leaving it alone keeps it). A third-party service (a "cloud" one) shows a privacy note and lets you choose whether the message text is included (public channels only by default). **Send a test and continue** sends a test before anything is saved; if it fails, the error is explained and you can fix the form, or continue without a successful test. Last, **What to receive here?** pre-fills the events: **Like Push** (the events Web Push receives, the default), **Alerts only** (radio offline or back, telemetry alerts, mentions, channel changes) or **Everything**. Change it later in the table below.

The `legacy:email`, `legacy:webhook` and `legacy:apprise` channels (your 4.18.x destinations) keep their address and options in the 4.18.x setting, which their sender still reads at each send; the rules decide what they receive like for any channel. Their card's **Edit** opens the same form as in 4.18.x (it saves as you leave a field), and **Remove this destination** empties it; their type is not offered again in **Add a channel**.

### ntfy channels

**Add a channel → ntfy** publishes to an [ntfy](https://ntfy.sh) topic: free, self-hostable, with Android, iOS and desktop apps, and it works for a Meshloom served over plain HTTP on your LAN. Subscribe to the same topic in the ntfy app.

- **Server**: `https://ntfy.sh` by default, or your own (`http://192.168.1.20:8080`; no user name or password in the address, use the fields below). A server on your private network, or on this machine, needs **Allow a destination on a private network**.
- **Topic** (required, write-only): 1 to 64 letters, digits, `-` or `_`. On ntfy.sh the topic is the only secret: anyone who knows it can read and post, so "topics are public by obscurity". Generate a long random one yourself, for example `openssl rand -base64 18 | tr '+/' '-_'` (24 URL-safe characters). Meshloom publishes the message as JSON to the server address: the topic is never part of a URL or a log line, and it is never shown again after saving.
- **Access token**, or **user name and password** (write-only), for a server with access control. A token wins when both are set. Changing the server asks you to enter the secrets again, so they never follow a new destination.
- **Priorities**: ordinary events use the default priority (3), urgent ones (*radio offline*, telemetry alerts, channel changes) the urgent priority (5, which rings on most phones). **Tags** are ntfy tags (emoji short codes show as icons).
- **Address of this Meshloom** (`https://meshloom.example.net/`): a tap on the notification opens the conversation or the node (no `#` part in the address).
- **Markdown** formats the message in the ntfy web app.
- **Certificate check**: it can only be turned off for a server whose every address is on a private network (a self-signed ntfy on your LAN); never for a public host.

Errors a test or the channel card shows: *credentials refused* (401/403), *quota reached* (ntfy.sh's daily allowance is spent: self-host ntfy or subscribe; Meshloom does not retry), *too many requests* (ntfy's `Retry-After` is honoured), *certificate error*, *service error* (a server error or a message that is too long).

ntfy.sh is a third party, and it keeps messages for 12 hours by default. A ntfy.sh channel therefore sends the text of Public and `#` channels only (title only for direct messages and private channels) until you choose **Always**, and shows a default limit of 10 messages a minute, 30 an hour and **200 a day**, under the free plan's 250 a day (check [ntfy.sh/pricing](https://ntfy.sh/pricing)). Meshloom enforces them (see [Limits and grouping](#limits-and-grouping)): a busy channel cannot spend ntfy.sh's own quota, and once the 200 are used nothing more is sent to the channel until the next UTC day. A self-hosted server has no daily limit by default.

### E-mail and webhook destinations

In **Settings → Notifications → Channels**, choose **Add a channel → Email** and enter an SMTP server (host, port, encryption, user, password, sender and recipients), or **Add a channel → Webhook** and enter an address with an optional HMAC secret. Secrets are not shown again after saving. Leave a secret field empty to keep it. The webhook address is a secret too, since it often holds a token: once saved, its field shows only the scheme and host; type a new address to replace it, or press **Clear** to remove it with its HMAC secret. The JSON backup download leaves them out (see [Security](/en/docs/deep/security/#integration-and-notification-secrets)). Each destination has a test button. This webhook is for Meshloom notifications. It is not the message webhook of fanout.

Meshloom refuses a webhook on a private network (your LAN, this machine, `localhost`, carrier-grade NAT, link-local addresses) unless you tick **Allow a destination on a private network (LAN, localhost)** under the address: Home Assistant, Node-RED or a script on your network need it. The check runs when you save the address and again before each send, and Meshloom then connects only to the address it checked, so a DNS answer that changes in between cannot redirect the call. Cloud metadata addresses (`169.254.169.254` and its equivalents) are always refused. If the host name cannot be found, type the IP address instead. A webhook saved before this check existed keeps working: the box starts ticked for it, and a one-time notice says so.

**Several recipients and a language (e-mail).** An e-mail destination takes several recipients, one per line (commas work too), and an optional Cc (each Cc address gets its own message, once); at most 20 addresses in all, the rest are ignored. Meshloom hands them to the mail server in one go: if the server refuses one recipient the whole notification counts as failed, even though the others may have received it, and it is not sent again. The message is written in the language of the channel (Automatic follows the notification language). When the text of a message is withheld (the setting that keeps message text out of third-party services), the subject reads "New message" and the body shows the sender and the conversation but not the text.

**Webhook headers.** Each webhook call carries `X-Webhook-Event`, `X-Webhook-Id` and `X-Webhook-Timestamp` (epoch seconds of that attempt), and `X-Webhook-Signature` (`sha256=` and the HMAC-SHA256 of the body) when a secret is set. The id is the same on every retry of one notification: a receiver such as Node-RED can ignore an id it has already seen. The signature covers the body only, so it did not change. The id and the timestamp are not signed: they help a receiver drop an honest retry, not a replayed request (a signed version is planned). Turning certificate checks off (for a self-signed certificate on your network) is accepted only for a destination on a private network; a public address always keeps its check. An e-mail login and password are never sent without encryption (mode **None**) to a mail server outside your private network.

The e-mail is HTML, with a plain-text copy. The Meshloom logo travels inside the message (an inline image), so opening the e-mail loads nothing from the Internet and contains no link.

### Apprise destination

The **Apprise** channel, added the same way, takes one [Apprise URL](https://appriseit.com/getting-started/universal-syntax/) per line. A few examples:

| Service | URL |
|---------|-----|
| ntfy | `ntfys://ntfy.sh/my-meshloom-topic` (or `ntfys://ntfy.example.org/topic` for your own server) |
| Telegram | `tgram://bot_token/chat_id` (the chat id is required) |
| Discord | `discord://webhook_id/webhook_token` |
| Home Assistant | `hassios://homeassistant.local:8123/long_lived_token` |

The URLs hold tokens, so they are a secret: after saving they read `********` and are never shown again; the JSON backup download leaves them out. Change them by typing new ones; empty the field to remove them. Apprise only accepts the services Meshloom has reviewed (the same list as the [Apprise fanout](/en/docs/deep/fanout/)); a URL it refuses is named by its line when you save. This destination carries the system notifications chosen in the table above; it is not the Apprise fanout, which forwards every message of the channels you pick.

- **Message text.** *Public and # channels only* (the default of this 4.18.x destination; a new Apprise channel starts on *Always*) sends the text of messages on the Public channel and `#` channels, and only a title for direct messages and private channels ("Message from Léa"). *Always* sends every text; *Never* sends titles only.
- **Format.** Plain text, or Markdown. In Markdown the message text is escaped (a message cannot inject a link or a heading), and in both formats room-wide mentions (`@everyone`, `@here`, `@channel`, `@all`, `@room`, `<@…>`, `<!…>`, `<#…>`) are neutralised.
- **Allow a destination on a private network (LAN, localhost).** Off by default, as for the webhook: tick it for an ntfy, Gotify or Home Assistant server on your network or on this machine. The check runs at save and again before each send.

Each URL is sent on its own: one failing service does not stop the others. A failed send is retried after 5 s and 30 s when the service is unreachable, answers a server error or asks to slow down (its `Retry-After` is honoured), never when it refuses the request (a wrong token, for instance). A URL with several targets (several ntfy topics, Telegram chats or e-mail recipients) is tried only once, so the targets already reached do not get the message twice. To protect your accounts, Meshloom sends at most one Apprise notification every 2 seconds, keeps 20 waiting and replaces the rest with a single "+N events"; new contacts and advertisements notify at most 10 times an hour, then one "+N new contacts". The last failure (its code, the URL line, its scheme and host only) is in `/api/health` under `notification_statuses["legacy:apprise"]` (see "How notifications are sent" below).

### Discord destination

The **Discord** channel posts to a Discord channel through a webhook (in Discord: channel settings, **Integrations**, **Webhooks**, **Copy Webhook URL**). Paste the URL in the channel's form; it must start with `https://discord.com/api/webhooks/` (or `discordapp.com`), any other address is refused. The URL holds the token, so it is a secret: it is never shown again after saving, nor in the JSON backup. An optional **Username** replaces the webhook's own name; Meshloom never changes its avatar.

Whatever a node or a message says, it cannot ping anyone: every request carries `allowed_mentions: {"parse": []}`, so `@everyone`, `@here`, users and roles are never notified, the text is escaped (no link, heading or emphasis injected, `@everyone` is broken) and link preview cards are turned off. Discord is a third party, so by default the text of messages from **Public and # channels** is sent, and direct messages and private channels give a title only ("Message from Léa"); change it with **Message text**. Certificate checks cannot be turned off (the address is public). Meshloom waits when Discord says the webhook is rate-limited and retries after the delay it gives; a deleted webhook shows as an authentication error.

### Preset channels (Telegram, Gotify, Home Assistant, Pushover, Matrix, Signal, Slack, Mattermost, Rocket.Chat, SMS)

These are **presets**: ready-made forms for services that Meshloom reaches through Apprise. You fill in named fields, Meshloom builds the Apprise address itself when it sends and checks it like any other destination. The secrets (the bot token, the Gotify token, the Home Assistant token, the Pushover keys, the Matrix credential, the Slack webhook address and so on) are write-only: they are never shown again, and Meshloom never stores the generated address.

**Telegram** (a third party: the text of direct messages and private channels stays home by default). Create a bot with `@BotFather` (send `/newbot`) and copy its token (`123456789:AAH…`). Get the **chat id**: send your bot a message, then open `https://api.telegram.org/bot<token>/getUpdates` in a browser and read `chat` → `id` (or ask `@userinfobot`); a group's id is negative (`-100…`), a public channel can be its `@name`. For a group or channel, add the bot to it first. **Topic id** is only for a group with topics. A chat id is required: without it the channel is refused. Default limit: 15 messages a minute, 200 an hour; when Telegram answers *too many requests* Meshloom waits the time it asks for.

**Gotify** (your own server). In Gotify, create an *application* and copy its token. Enter the server address (`https://gotify.example.org`, a path prefix is kept; `..` and `%` are refused in it) and the token; the priority applies to every notification. A server on your home network needs **Allow a private-network destination** ticked, and changing the server asks you to enter the token again. Default limit: 60 messages a minute.

**Home Assistant** (your phone's Companion app, or any `notify` service). In your Home Assistant profile, create a **long-lived access token**. Enter the address of Home Assistant (`http://homeassistant.local:8123`; only the address, a path such as `/ha` is refused), the token and the **notify service**: `mobile_app_your_phone` (find the exact name in Home Assistant, Developer tools, Actions; `notify.alerts` works too). Home Assistant is nearly always on your network, so tick **Allow a private-network destination**; the error says so when it is needed. Default limit: 60 messages a minute.

**Pushover** (a third party). On pushover.net, copy your **user key**, create an *application* and copy its **API token** (both 30 characters). Optional **Devices** limits delivery to those device names (comma separated, up to 10); **Priority** and **Sound** apply to every notification (*emergency* priority repeats until acknowledged). A free application may send 10,000 messages a month: the default limit (10 a minute, 100 an hour) stays well below it, but the monthly quota is yours to watch.

**Matrix** (a third party; messages are sent as Markdown, so text from the mesh is escaped). Use a dedicated account or bot that has joined the room. Enter the homeserver address (only the address, no path; `https://matrix.example.org`), then either an **access token** alone (leave the user name empty) or the account's **user name** and **password** in the credential field (one of the two is required: saving or testing without it is refused with `config_required`, before anything is sent), and the **room**: its id (`!abc:example.org`, room settings, Advanced) or alias (`#room:example.org`). A homeserver on your home network needs **Allow a private-network destination**; changing the server, the user or the room asks you to enter the credential again. Default limit: 30 messages a minute.

**Signal** (your own server; needs a [signal-cli REST API](https://bbernhard.github.io/signal-cli-rest-api/) server with a registered number, usually on your home network, so tick **Allow a private-network destination**). Enter its address, the sending number and the recipients (`+33611111111,+33622222222`, up to 10, with country codes). The user name and password are only for a reverse proxy that asks for basic authentication. Default limit: 20 messages a minute, 200 an hour.

**Slack** (a third party). In your Slack app enable *Incoming Webhooks*, add one to a channel and copy its address `https://hooks.slack.com/services/AAA/BBB/CCC` and paste it whole in the **Webhook address** field (it holds the secret, so it is never shown again; only `hooks.slack.com` addresses are accepted). Default limit: 30 messages a minute, one a second.

**Mattermost** (your own server; Markdown). In Mattermost, *Integrations*, *Incoming Webhooks*, create a webhook and copy the key at the end of its address (`…/hooks/<key>`). Enter the server address (a path prefix is kept) and the key; a server on your home network needs **Allow a private-network destination**. Default limit: 30 messages a minute.

**Rocket.Chat** (your own server). Use a dedicated account that has joined the room. Enter the server address (no path), the **user ID or user name** and the password (with a personal access token, set the credential type to *token* and enter the user ID Rocket.Chat shows next to the token, not the user name) and the room: `#channel`, `@user` or a room id. A server on your home network needs **Allow a private-network destination**. Default limit: 30 messages a minute.

**SMS Free Mobile** and **SMS SFR** (third parties; text policy locked on **Never**). An SMS holds about 160 characters, so these channels are fixed on a title only (for example "Message from Léa"): **Message text** is locked on **Never**, and the server refuses another value (`text_policy_locked`), because the service would cut the text. Free Mobile sends only to the line of the account that enabled **Notifications par SMS** (your 8-digit login and the key the option shows); SFR needs an account of its messaging service with the SMS API (account name, password, space id) and takes the recipient numbers. Both default to 2 messages a minute, 20 an hour and 100 a day, sent 10 seconds apart, to avoid the services' own anti-flood refusals.

Each form has a **Test** that sends a message before anything is saved.

## Privacy and what leaves your machine

Notifications leave your Meshloom for the service behind each channel. Three things decide what they contain.

**The kind of conversation.** Messages on the Public channel and on `#` (hashtag) channels are readable by anyone who knows the name: the key comes from the name. Direct messages and channels with a private key are confidential.

**The text policy of the channel.** Each channel has **Message text** (or **Include message text**) with three values:

- *Public channels only*: the text of Public and `#` channels is sent; direct messages and private channels give a title only ("Message from Léa").
- *Always*: every text is sent.
- *Never*: a title only, for everything.

The table of [Channels](#channels) gives the starting value of each type. Discord, Telegram and ntfy.sh show the choice in their form, with a privacy note; the other types do not show it, and start on *Always* (or *Public channels only* for an e-mail relay outside your network). The 4.18.x Apprise destination keeps its own default, *Public channels only*, in its form. **Web Push has no text policy**: its payload is encrypted for the browser and always carries the text. If a third-party service is on a generic Apprise channel, set its text policy through the API (`include_message_text`), since the form does not offer it.

**The service's own memory.** Public services keep what they receive: ntfy.sh caches messages (12 hours by default) and anyone who knows the topic can read them, so on ntfy.sh the topic is the only secret and must be long and random. Telegram and Discord keep their history for as long as the chat exists. Deleting a notification on your phone does not delete it there.

When you send other people's messages to such a service, **you**, the operator of this Meshloom, are responsible for that data (in the EU, the data controller); Meshloom is software you installed, not a processor. Nothing goes through TwinRocket.

Text from the mesh is unauthenticated: anyone in range can write anything. The shared renderer therefore breaks `@everyone`, `@here`, `@channel`, `@all`, `@room`, `<@`, `<!` and `<#` with an invisible character in every notification sent through the ntfy, Discord, Apprise and preset channels (Telegram, Gotify, Home Assistant, Pushover, Matrix, Signal, Slack, Mattermost, Rocket.Chat, SMS), plain text or Markdown. In Markdown (Discord, Matrix, Mattermost, ntfy with Markdown on, Apprise in Markdown format) it also escapes Markdown punctuation so a message cannot inject a link or a heading. E-mail, Web Push and webhook carry the text as it is: no chat service reads a mention there (the e-mail is HTML-escaped). The Discord channel also sends `allowed_mentions: {"parse": []}`, so it pings nobody.

**Shared images and links.** A message that is only a short link or a shared image (`📷 https://meshl.ink/…`, `🔒 …`, see [Short links and photos](/en/docs/deep/share/)) never reaches a push notification, an e-mail, or an Apprise, ntfy.sh or Discord message as an address: it reads `[image]`, `[encrypted file]` or `[link]` (with the caption, if any). This comes from the text alone: Meshloom fetches nothing to write it, and any `#fragment` after a link is dropped, never shown. In a channel the sender stays: `Léa: [image]`. The webhook channel sends the raw event, text included.

The list of secrets is below in [Backups](#backups). In short: the secrets of channels are write-only, the API never returns them, and no backup contains them (the full backup does hold the Web Push private key and the subscriptions, see [Backups](#backups)).

## Destinations on your network

By default Meshloom refuses to send a notification to an address on your local network, to this machine (loopback, `localhost` included), or to a cloud metadata address. This way a changed setting cannot turn the server into a probe of your network. A Gotify, ntfy, Home Assistant, Node-RED or mail server at home, or on the same machine as Meshloom, is therefore refused at first, with the message *this address is on a private network*.

If the destination is yours, tick **Allow a private-network destination** in the channel's form (it is off for each new channel). It covers loopback too. The address is checked when you save the channel and again before each send, and Meshloom connects only to the address it checked. Cloud metadata addresses (`169.254.169.254` and its equivalents) stay refused even then. If a host name cannot be found, type its IP address instead. Turning the certificate check off is accepted only for a destination whose addresses are all private. The browsers' own push services have a separate switch, see [Push services on a private network](/en/docs/deep/push/#push-services-on-a-private-network).

The destinations you had before the check existed (4.18.x) are allowed to stay where they are: the upgrade ticked the box for them.

## The rules table

**Settings → Notifications → Defaults for all accounts** is a table: one row per event, grouped (Messages, Nodes, Radio, Telemetry, Meshloom), one column per channel (Web Push and the channels you added; a switched-off channel is greyed but keeps its ticks) and an **Always deliver** column that lets an event through the pause. Tick a box to send that event to that channel. The first column stays in view when the table scrolls sideways on a phone. Every box has a label, so the table can be used with the keyboard and a screen reader. **New node seen** has no box of its own, only one sub-row per node type (companions, repeaters, sensors). **Channel changed** always goes to Web Push (locked). **Mentions** has a **Match whole words only** tick. Web Push is on for the usual events at the start.

A new channel is filled in for you by the last step of the add dialog, **What to receive here?**: **Like Push** (the events Web Push receives, the default), **Alerts only** or **Everything**. The urgent events (radio offline, telemetry alerts, channel changed) are always delivered, even during **Do not disturb**.

## Your own rules

The table above holds the **defaults**. Web Push is the one channel whose recipients are people (every device belongs to an account), so under **Settings → Notifications → My notifications** each account (member or administrator) sees one line per event, and one per node type for *New node seen*. Each line follows the administrator's default (the list says whether that is *notify* or *do not notify*) until you choose **Always notify me** or **Never notify me**; **Follow the defaults again** clears all your choices. Your choices apply only to the devices of your account, and nobody else sees or edits them.

- *Never notify me* wins over everything for your devices, the administrator's conversation exceptions included.
- *Always notify me* still respects what belongs to the installation: the administrator's exceptions for a conversation, a muted channel, the block lists and **Do not disturb**.
- E-mail, webhook, ntfy, Telegram and the other channels are shared destinations that the administrator owns: they keep following the table, whatever your choices.
- A mention reaches your devices as a mention when your rules give you *Mentions*, else as an ordinary channel message, never both.
- The summaries sent when a channel is over its limit (see [Limits and grouping](#limits-and-grouping)) follow your rules too: you only get one when at least one of the notifications it replaces was meant for you, and its counts only include those. The notice that a destination changed reaches every device.
- Until you change something, nothing differs from the table, and an upgrade to this version changes nothing for anyone.

### Your own conversation exceptions

Under **My notifications → Conversations**, pick a channel or a contact and choose **Mute for me** or **Always notify me**. It applies to that conversation only, on your devices only, and wins over your own event lines above (a channel muted for you stays silent even if *Channel messages* is on; a contact you always want to hear from notifies even if *Direct messages* is off). *Mute for me* also silences mentions of that channel. *Always notify me* does not override what belongs to the installation: a muted conversation, a blocked sender and the administrator's pause still keep it quiet. Change the choice in the list, or remove the line to follow your event lines again. Up to 256 conversations.

### Pause your own devices

Under **My notifications → Pause**, choose **1 hour**, **8 hours**, **Until tomorrow 8:00** or **Until I resume**. Your devices then receive nothing, except urgent alerts (radio offline, telemetry alerts) and the events the administrator marked *Always deliver*, as with **Do not disturb**. The summaries are paused too. **Resume now** ends it early; a pause with an end time ends by itself. Nobody else is affected, and neither are the shared destinations (e-mail, webhooks...): the administrator's own **Do not disturb** is separate. **Follow the defaults again** clears your rules and conversation exceptions but does not end a pause in progress.

The same choices are available through `GET`, `PATCH` and `DELETE /api/push/my-rules`, and `PUT` and `DELETE /api/push/my-pause` (see [Web Push](/en/docs/deep/push/)).

## Events

| Event | When it fires |
|-------|---------------|
| Direct messages | A direct message arrives, including posts from room servers |
| Channel messages | A message arrives on the Public channel or a `#` channel. A channel with its own key stays silent unless it has an exception |
| Mentions | A channel message names you, see [Mentions](#mentions) |
| New node seen | A node is heard for the first time, with one sub-row per type: **Companions** (a person's radio), **Repeaters** and **Sensors**. Only a new contact added after the radio finished its start-up counts: a node you already know, an unknown node and a room server never trigger it |
| Channel found | Meshloom finds a new hashtag channel |
| Telemetry alert | A tracked repeater or contact crosses an alert threshold. The thresholds and the channels that receive them (a chip per channel) are set on the **Alerts** page |
| Update available | A new Meshloom version is available |
| Radio offline | The radio has been unreachable for longer than the delay set below the table (60 s by default). Sent as urgent (the e-mail subject starts with `[URGENT]`), once per outage |
| Radio back online | The radio is reachable again after a **Radio offline** notification, with how long it was down |
| Meshloom started | Meshloom has just started: its version, whether the radio connected and how fast. On by default for push |
| Channel changed | A notification channel is created, deleted, switched on or off, or changed in its settings (a new address, server or webhook, other options; not a rename or a new password). It always goes to Web Push and cannot be taken off; other channels follow the table, and the channel that changed never hears of it |
| Destination changed | An e-mail, webhook or Apprise destination is changed through the 4.18.x settings. Web Push only |

**Radio offline** and **Radio back online** never fire for a reboot Meshloom started itself (clock fix, Wi-Fi change) nor while the connection is paused in **Settings → Radio**; pausing ends an outage without a notification. Nothing is sent before the radio has connected once since Meshloom started, nor during the same delay right after a start, so an update or a restart does not raise a false alarm. Set the delay under the **Radio offline** row of the table (`radio_offline_grace_s` in `/api/settings`; 0 notifies at once, up to 3600 s). The radio is checked about every 5 seconds, and a reconnect attempt in progress can delay the check, so the notification may arrive a little after the delay. If Meshloom restarts during an outage, the downtime still counts from the real start.

**Radio offline cannot warn you when Meshloom itself is down** (power cut, host reboot): nothing is running to send it. **Meshloom started** makes an unexpected restart visible. To be warned while it is stopped, use the Home Assistant `binary_sensor.<radio>_connected` with a “for” delay (see `README_HA.md`); a periodic heartbeat is planned.

## Mentions

A channel message **mentions you** when it contains `@[` + your radio's name + `]` (for example `@[Bob]`), in any case. With **Match whole words only** ticked (row *Mentions* of the table), your radio's name written as a whole word also counts: *Al* matches "hello al!" but not "Alpha". A mention is sent to the channels ticked on the **Mentions** row, instead of the ordinary channel notification on those channels, so a channel never receives both; it reads "Mention in #paris by Léa" and replaces the ordinary notification of that conversation in the tray. Channels where **Mentions** is not ticked keep receiving the ordinary notification. The mute of a channel, the block lists and Do not disturb apply as for any message, and a mention is **never urgent**. It is on by default for the channels that receive direct messages.

**Mentions can be forged by anyone in range:** channel messages are not authenticated, so anyone who writes `@[your name]` makes your radio "mentioned". That is why a mention is never urgent, and why, on a **private channel** (one with its own key), it only reaches channels that are not a third party (Web Push, a self-hosted ntfy, an e-mail relay on your network...) or channels you explicitly allowed for that conversation with an exception (**on**) in the conversations list; an exception set to **on** receives the mention even if the channel is not ticked on the **Mentions** row. Apprise and webhook channels, and the presets built on Apprise (Telegram, Gotify, Home Assistant, Pushover and the others), always count as a third party, since the type cannot tell where they send. Direct messages are not mentions.

## Conversations and exceptions

Without an exception, the table decides: a direct message follows the **Direct messages** row, the Public channel and `#` (hashtag) channels follow the **Channel messages** row, and a channel with its own key notifies no one. An **exception** is a choice made for one conversation and one channel (*on* or *off*); it wins over the table, except for the mute, the block lists and a pause (see below). Exceptions are made with the bell.

### The bell, for one conversation

In the header of a conversation, the bell (administrators only; there is none for the other roles) opens a menu with one tick box per enabled notification channel, with its type icon, its name and a status dot (connected, error, paused, disconnected). A box shows whether the channel notifies this conversation, and the grey word beside it says why: *default* (the rows of the table decide), *on here* or *off here* (an **exception** made for this conversation only). Ticking a box that the default already leaves ticked, or unticking one the default leaves empty, makes the conversation follow the default again and removes the exception. If your browser is not subscribed yet, switching **Web Push** on subscribes it first. The bell is filled while at least one channel notifies the conversation. It shows the configuration: a muted channel or an active Do not disturb still leaves the boxes ticked while no notification is sent.

The same menu has the **Do not disturb** shortcut (1 hour, 8 hours, until tomorrow 8:00, **Resume now**: the same global pause as in the settings) and a **Configure notification channels** link. In the conversation list, the same menu is under the three dots of a row, **Notifications**.

Exceptions are listed in **Settings → Notifications → Conversation exceptions**, one line per conversation with a button per channel: click it to cycle between *inherit* (the event rule), *on* and *off*. The cross removes the exception.

## Do not disturb

At the top of the rules in **Settings → Notifications**, **Do not disturb** pauses every notification for **1 hour**, **8 hours** or **until tomorrow 8:00** (your browser's clock), and **Resume now** ends it. The time left is shown. Events ticked **Always deliver** (a column that is always ticked and locked for the urgent events: radio offline, telemetry alerts and channel changes) still go through. The pause is shared by every browser and channel; it is written to the audit log. A notification that arrives during a pause is not sent later: the delivery log records it as *paused*. It is only counted, and when the pause ends each channel gets one summary message ("During the pause: 3 messages in 2 conversations…", the same kind of summary as quiet hours, never a message text). The summary is sent only while the summary option of the quiet hours is on (it is by default). A channel's own pause does not produce a summary.

## Quiet hours

Quiet hours are a recurring window (for example 22:00 to 07:00) during which notifications are held instead of sent, then summed up in one message when the window ends. Set them in **Settings → Notifications**, in the **Quiet hours** group below *Do not disturb*: the switch, the start and end times, the days and the summary option. (The same fields exist in the rules API: `quiet_hours` in `PATCH /api/notifications/rules`, with `enabled`, `start` and `end` as `HH:MM`, `weekdays` from 0 = Monday to 6 = Sunday, `digest_at_end`.) The times are read in the time zone of the machine running Meshloom, which `GET /api/notifications/rules` returns as `host_timezone`. An overnight window belongs to the day it opens on, and equal start and end mean the whole selected day.

- **What goes through.** The urgent events (radio offline, telemetry alerts, channel changed), the events ticked **Always deliver**, and every channel whose **Exempt from quiet hours** box is ticked (on its card in the channel list; ticked by default for a webhook created from now on, unticked for the other types). The webhook imported from the 4.18.x settings (`legacy:webhook`) is unticked, because quiet hours did not exist then and are off until you enable them: tick its box if it must keep receiving. Everything else is held on the other channels, and the delivery log records it as *suppressed_quiet*.
- **The summary.** Per channel, one message once the window is over (checked every minute): "During quiet hours: 14 messages in 3 conversations (#paris 9, Léa 3, #test 2), 1 new contact". It holds counts and names, never a message text. Public and `#` channels are always named; a direct message or a private channel is named only on a channel whose text policy is *always*, else it is only counted. The counts are kept in the database, so a restart during the window loses nothing. With the summary option off, nothing is counted and no summary is sent (the same option governs the summary after *Do not disturb*). The same event arriving twice during the window (same identity) is counted once.
- **With the other pauses.** The order is: your own choices and the administrator's rules decide who wants the event (a muted conversation, an event an account turned off, or an account paused for itself is never counted); then **Do not disturb** and a channel's own pause hold it (*paused*; only the global pause is counted for its own summary); then the quiet hours. **Always deliver** and urgent events skip both. *Always notify me* on a conversation does not skip the quiet hours. While **Do not disturb** is on at the end of the window, the summary waits for it to end. A summary that could not be queued (a rate limit, a channel paused) is kept and tried again every minute: ten minutes at most when it was refused (a rate limit), with no limit while the channel is paused for itself (the summary leaves a minute after the pause ends); one that was queued and failed with a temporary error gets one more attempt five minutes later. On Web Push the summary reaches only the accounts that would have received at least one of the held events.

## Mute and block lists

On a channel, the mute button silences everything for a time you choose (15 minutes up to 24 hours, or indefinitely). It also hides the unread counter. It overrides defaults and exceptions, and it is not the same control as the bell.

Messages from a blocked contact or name, and messages on a muted channel (until the mute ends), never notify on any channel: Web Push, e-mail, webhook and Apprise alike. Only an event marked **Always deliver** still goes through. Nothing changes until you block someone or mute a channel.

## Language

**Settings → Notifications → Notification language** sets the language of the instance for notifications: **Automatic**, **Français** or **English**. Automatic follows the browsers subscribed to push: English only when most of them (a strict majority) use English, French on a tie. Each channel also has its own **Language of the notifications** in its form (Automatic uses the instance's language). Web Push notifications always keep the language of the browser that subscribed, and the webhook sends data, not text.

## Proof of change

Creating, deleting, switching on or off a channel, or changing where it sends writes a line in the audit log and sends the **Channel changed** notification to every subscribed browser and, by default, to the other enabled channels, naming what changed. The Web Push copy cannot be turned off: if you did not make the change, someone else did. Changing a 4.18.x destination through the old settings sends **Notification destination changed** to Web Push in the same way.

## How notifications are sent

Each channel has its own queue and sends one notification at a time, so a slow or failing service never delays the others, nor push. Sends are spaced: at least 10 seconds apart for e-mail, 3 for Telegram, 10 for the SMS presets, 2 for Discord and Apprise, 1 for ntfy, Pushover and Slack. How many may go out is set by the channel's [limits](#limits-and-grouping). A Web Push or e-mail send is never retried, and Apprise and the presets retry each URL themselves (0, 5 and 30 s) without the later extra try below. For the other types, a send that fails because the service is unreachable, times out, answers a server error or asks to slow down is tried again after 5 s and 30 s (its `Retry-After` is honoured, up to 2 minutes), never when it refuses the request; Web Push and e-mail are not retried. A message notification that still fails is dropped (it would arrive too late); a radio offline or telemetry alert gets one more try 5 minutes later when the service was unreachable, never after it refused the request. Notifications still waiting when Meshloom stops are not sent after the restart.

The state of each destination is in `/api/health`, under `notification_statuses`, keyed by its id (`builtin:push`, `legacy:email`, `legacy:webhook`, `legacy:apprise` and the id of each channel you add): `status` (`connected`, `error`, `paused` or `disconnected`), the last error as a code, never a password or a token (administrators only), and the time of the last successful send. Meshloom keeps a short log of failed, grouped, dropped and cancelled notifications, without their text; successful sends are only counted.

The test of an Apprise channel sends a test notification to every URL; a failure names the URL line and the kind of failure, never the token.

### Limits and grouping

Every channel has limits per minute, per hour and, for some, per day. The defaults come with the type (Telegram 15 a minute and 200 an hour, Pushover 10 and 100, SMS 2 a minute, 20 an hour and 100 a day, Discord 30 and 600, e-mail 6 a minute, 60 an hour and 200 a day, webhook 120 a minute, Web Push 60 a minute, ntfy.sh 10 a minute, 30 an hour and 200 a day); a channel can have its own. Nothing is lost silently:

- **A full minute or hour limit does not drop notifications.** They wait one minute, then go out as **one summary** such as "+14 messages: 12 in #paris, 2 DMs". The summary only counts: it never carries a message text, a sender or the name of a private channel (those are counted as "in other channels"). If the hour limit is still full, the summary waits for room.
- **Urgent events** (radio offline, telemetry alert) take a token like any other notification, but go through a full limit once for each event: a sensor that keeps flapping cannot get past the limit, its repeats join the summary.
- **A daily limit is a quota.** When the day's allowance is used (counted from midnight UTC, and kept across restarts), nothing more is sent to that channel until the next UTC day: no summary either, and each refused notification is logged as *quota exceeded* in the delivery log.
- **Global cap.** Over all channels together, Meshloom sends at most 600 notifications an hour (`limits.global_per_hour` in the rules). Above it nothing is sent, urgent events included, each is logged as *rate limited*, and `/api/health` reports `notifications_capped: true` until the hour has room again.
- **No repeats.** A new contact notifies once a day, a radio outage once, a message once, even after a restart. A telemetry alert is only limited by its own hysteresis.

## Testing a channel

Every channel has a **Test** button, and the add dialog tests **before anything is saved** (**Send a test and continue**). A test goes through the channel's own sender and the same checks as a real send, without joining the queue, the journal or the counters. A failure is shown as a short code, translated, that never contains an address, a token or the network's own error text:

| Code | Meaning |
|------|---------|
| `invalid_config`, `config_required` | A field is wrong or empty; the field is flagged |
| `text_policy_locked` | The message text policy of an SMS channel cannot be changed (it stays on Never) |
| `secret_required` | You changed where the channel sends, so the stored secret is not reused: type it again |
| `egress_scheme`, `egress_private_address`, `egress_unresolvable`, `egress_host_not_allowed` | The address is refused: wrong scheme, a private network not allowed (see [Destinations on your network](#destinations-on-your-network)), unknown host name (type the IP address), or a host the type does not accept |
| `verify_tls_requires_private_destination`, `smtp_plaintext_auth_refused`, `tls_error` | The certificate check can only be turned off for a private destination; a mail login is never sent without encryption outside your network; the secure connection failed |
| `auth_failed` | The service refused the token, password or key (HTTP 401 or 403) |
| `rate_limited`, `quota_exceeded` | The service asked to slow down, or the day's allowance is spent (ntfy.sh) |
| `timeout`, `service_error` | No answer in time; or the service answered with an error |
| `apprise_url_invalid`, `apprise_chat_id_required` | An Apprise address is refused (the line is named) or a Telegram address lacks its chat id |

Tests are limited: one every 5 seconds per channel, one every 2 seconds for a configuration not saved yet, and 10 a minute in all. Beyond that the answer is `429 rate_limited`. The result of a test on a saved channel also updates its status on the card.

## Backups

The **full backup** (Settings → Database) has a *Notifications* part: every channel (type, name, options, limits, language, text policy) and the rules (events, conversation exceptions and the options of the rules table, quiet hours included), **without any secret**. Each account's own choices (see [Your own rules](#your-own-rules)) travel with the accounts, so they come back when **Include accounts** is checked. Passwords, tokens, a Discord or Apprise address, a ntfy topic, a webhook URL and its HMAC secret never leave Meshloom. The e-mail, webhook and Apprise destinations of the old settings are also written without their secrets. The block lists travel with the settings. The delivery journal and counters are not saved, and neither is a pause: *Do not disturb* is never restored. The Web Push keys and subscriptions are a separate part, restored only on a Meshloom with no subscription of its own.

**Restoring.** A channel that lost any secret comes back **switched off** and marked *Secret missing after a restore*; Meshloom never sends with a missing secret. Open it in **Settings → Notifications → Channels**, type each secret again (or clear it on purpose), then switch it on. If the same channel already exists here with the same destination, its stored secrets are kept. Restoring merges the channels (one the file does not name stays) and replaces the rules. A backup made by Meshloom 4.18 restores into the same channels and rules (Web Push, e-mail, webhook, Apprise, the exceptions of each conversation).

Encrypt the full backup with a passphrase anyway: it still holds the Web Push private key and subscriptions, the secrets of your integrations (MQTT, fanout webhooks, SQS) and, if you include them, the accounts. See [Back up your radio](/en/docs/backup/) and [Security](/en/docs/deep/security/#integration-and-notification-secrets).

## Configure channels from a trusted network

A channel decides where your messages go. Whoever can create or edit one can send a copy of your notifications to a service of their choice. That is why every route under `/api/notifications` needs an **administrator**: other roles can neither see nor change a channel (a member only subscribes its own browser to push). Give the administrator role only to people you trust with the node, sign in over [HTTPS](/en/docs/deep/https/) beyond a network you trust, and keep your [Allowed origins](/en/docs/deep/security/) list short. Any change to a channel's destination sends a Web Push notification to every subscribed browser (see [Proof of change](#proof-of-change)).

On a Meshloom older than 5.0 there was no sign-in at all: anyone who could reach the page could configure a channel. Configure channels from a network you trust.

## Upgrading and downgrading

**From 4.18.x.** On the first start Meshloom turns your destinations into channels: the built-in **Web Push** channel, and **`legacy:email`**, **`legacy:webhook`** and **`legacy:apprise`** for the ones you had configured, and it turns your push defaults and conversation exceptions into rules. Nothing changes in what you receive. Those three `legacy:` channels are still edited with their own form on their card (the one of 4.18.x, saved as you leave a field), and their type is not offered again in **Add a channel**. You can switch them on and off, rename them and change their rules like any channel, but you cannot delete them from the channel API or change their address or options there: the API answers **`409 legacy_managed`** for a `PATCH` of `config`, `allow_private_network` or `verify_tls`, and for a `DELETE`, of a `legacy:` channel. Use **Remove this destination** on the card instead. A medium that was never configured (no SMTP server, no URL) does not become a channel. A conversation exception for it is kept and applies once you configure it.

**Deprecated endpoints.** `GET`/`PATCH /api/push/preferences`, `PUT /api/push/preferences/conversations/{key}`, `notification_destinations` in `PATCH /api/settings` and `POST /api/settings/notification-destinations/test` still work as in 4.18.x. They answer a `Deprecation: @1792368000` header (RFC 9745) and a `Link` to the rules endpoint, and **will be removed in 5.2.0**. Every change they make is copied into the rules, and the other way round, for the four media push, e-mail, webhook and Apprise; the other channels are invisible to them. Asking for e-mail, webhook or Apprise on a conversation when that destination is not configured answers `400 channel_missing`. Move scripts to `/api/notifications`.

**Apprise in fanout.** The Apprise output of [fanout](/en/docs/deep/fanout/#apprise) is not converted: it keeps working and keeps forwarding every message. Create an Apprise channel (or a preset) for notifications; a one-click conversion is planned.

**Downgrading.** Going back to a version older than 5.0 reads the old settings again. **Channels added after the update are not visible after a downgrade**, the rules you changed in the table are not read, and a channel that was not a 4.18.x destination (ntfy, Discord, Telegram, Gotify, Home Assistant, a second e-mail or webhook) stops notifying. Note down the destinations you need before you go back, and restore from a backup taken before the update if you want the old state exactly.

## When nothing arrives

1. Look at the channel's card in **Settings → Notifications → Channels**: the status dot (working, error, paused, off) and the last error. A channel restored without its secret is off and says *Secret missing after a restore*.
2. Press **Test** on the card. See [Testing a channel](#testing-a-channel) for the codes. A private network that is not allowed is refused before any connection.
3. Check the rules table: the event must be ticked for that channel. A mute, a block, a **Do not disturb** in progress and a conversation exception override it (see [Mute and block lists](#mute-and-block-lists)); channels with a private key stay silent unless an exception turns them on.
4. Is the channel switched on? Is **Do not disturb** active? Only events marked **Always deliver** pass it.
5. For Web Push, see [When nothing arrives](/en/docs/deep/push/#when-nothing-arrives) on the push page: HTTPS, browser permission, Apple.
6. Look at the server log and at `GET /api/notifications/deliveries`, which lists failures, notifications dropped and ones skipped during a pause, without their text. See [Troubleshooting](/en/docs/deep/troubleshooting/).

## Endpoints

Administrators manage the channels under `/api/notifications`:


| Method | Endpoint | Effect |
|--------|----------|--------|
| GET | `/api/notifications/types` | The channel types: JSON Schema (secret fields are `writeOnly`), default limits |
| GET, POST | `/api/notifications/channels` | List and create channels (secrets read `********`) |
| POST | `/api/notifications/channels/test` | Test a configuration before saving it (nothing is stored) |
| GET, PATCH, DELETE | `/api/notifications/channels/{id}` | Read, change (the type cannot change) and delete a channel; `builtin:push` cannot be deleted (`403 builtin_channel`) |
| POST | `/api/notifications/channels/{id}/test` | Send a test; one per 5 seconds per channel, then `429 rate_limited` |
| GET, PATCH | `/api/notifications/rules` | Channels per event, `always_deliver`, the global pause (`paused_until`), the radio offline delay |
| PUT | `/api/notifications/rules/conversations/{key}` | Channels of one conversation (`{"channels": {"id": true}}`, or `null` to clear) |
| GET | `/api/notifications/deliveries` | The delivery journal (failures and suppressions, never the text) and hourly counters |
| GET | `/api/notifications/events` | The event catalogue |


To keep a secret when changing a channel, leave it out, send `null` or send `********`; `""` clears it. Errors are `{"code": ..., "params": ...}` and never contain a host, an address or an exception text. Creating, deleting, enabling or disabling a channel, or changing where one sends, notifies Web Push (this cannot be switched off) and the other enabled channels, and is written to the audit log.
