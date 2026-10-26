## [Unreleased]

The server now enforces the radio's message size limit for every way of sending.

### Before you upgrade

Release 5.0.0 changes your database in ways an older version cannot undo. **Before upgrading, download a full copy of the database** (**Settings → Database → Download meshcore.db**) and keep it: there is no way back to 4.18 without it. A backup file made on 5.0.0 is not fully understood by 4.18 either (see below). The download is scrubbed: no account password or two-factor secret, no extension secret, no sign-in session, and the sharing and Store consents are reset. Restoring it on 5.0.0 keeps the accounts but nobody can sign in with a password until you run `meshloom-admin`, and extensions ask for their secrets again. For a complete restore, also keep a full backup (**Settings → Database → Backup & Restore**), which carries the accounts and the extensions (secrets still excluded).

### Breaking

- **Python bots are converted into local extensions** and no user code runs inside the server process any more (the `exec` is gone). Each converted extension stays off until an administrator reviews its permissions. The `bot` fanout type answers `410 bot_type_retired`, the "Unprotected bot execution" warning and its **Disable Bots Until Server Restart** button and route are gone. The conversion shim and the copy of the original bots (`fanout_configs_bots_archive`) are removed in 5.2.0. Details under *Changed* and in [Plugins](docs/user/en/deep/plugins.md#bots-from-418-and-earlier).
- **`MESHCORE_DISABLE_BOTS` and the add-on option `disable_bots` are removed, with no alias.** A leftover value locks nothing: it is logged with a start-up warning that names `MESHCORE_DISABLE_PLUGINS` (add-on: `disable_plugins`), the lock of every extension. The only use left is the one-shot migration of the bots, which stores the converted extensions switched off when the variable (or, in the add-on, `disable_bots: true`) is still there.
- **No rollback to 4.18.** The migration of the bots runs at the first start and an older version does not read the result: the converted bots do not appear as bots, and notification channels added in 5.0.0 are not seen. Restore the database copy taken before the upgrade to go back.
- **The full backup is format v2** (`meshloom-backup-v2`: identity, radio, Wi-Fi, accounts, notifications, extensions). 4.18 reads it as a v1 file and silently drops everything it does not know: restore a v2 file on 5.0.0 or later.
- **The server enforces the radio's message size limit** for every way of sending (bots, scripts, API). A message over the limit is now refused by the server; until now only the web composer checked.
- **`bots_disabled` and `bots_disabled_source` in `/api/health` are deprecated**, kept for one release.
- **Extensions need Linux or macOS.** On Windows the extension system is switched off (`plugins_disabled_source: "platform"`). Without bubblewrap and without a dedicated account (macOS, Linux without them), extensions run at the weaker *hooks only* level, and the server log says so at every start.

### Added

- **Bundled extensions update with Meshloom.** At start, the extensions that ship with Meshloom (Echo, Auto-responder) and that you installed are updated in place when the release carries a newer version: settings, secrets, conversations, limits and data are kept. If the new version asks for more permissions it is updated but held, *Needs attention* / *Review permissions*, until you accept them; otherwise it keeps running. A package that cannot be installed keeps the old version and is logged, and Meshloom starts anyway. Nothing is updated while extensions are disabled by the host. English and French user guide updated.
- **Two bundled extensions, installable offline.** **Settings → Extensions → Installed → Add a bundled extension** now offers **Echo** (`!pling` answers `Plong!`, the reference plugin for writing your own) and **Auto-responder**, a no-code responder: one rule per line (`prefix,channel=#test: !ping => Pong, {sender}!`) with keyword, prefix and regex triggers, a channel filter, `{sender}`, `{channel}` and `{text}` in the reply, a pause per rule and per person, a most-answers-per-hour cap kept under the plugin quota, and a restricted regex dialect (checked on the parse tree: no nested repeats or repeated choices, at most two large repeats, no back-references or lookarounds, 100 characters, read on the first 200 characters of a message). They are ordinary plugins (same package format, sandbox, quotas and host guards), shipped in `meshloom_plugin/official/` and versioned with Meshloom. English and French.
- **Custom extension repositories.** **Settings → Extensions → Store** (administrators) adds an extension published on GitHub by typing its repository as `owner/name`; its latest release must carry `manifest.json`, `plugin.zip` and `plugin.zip.sha256`. These extensions are neither reviewed nor signed by Meshloom: the `.sha256` file only checks that the download is intact, and there is no online revocation for them, which the tab and the consent screen say. Nothing is fetched while the Store switch is off; only `github.com` and GitHub's download servers are contacted. Installed copies carry a *Custom* badge. Removing a repository keeps the extensions installed from it. English and French. Details in [Plugins](docs/user/en/deep/plugins.md#custom-repositories).
- **Pause all extensions.** **Settings → Extensions** has a **Pause all extensions** button (administrators, after a confirmation), the switch for an incident: every running extension stops, none starts while paused, events are not delivered to them and *Test with a message* answers `409 plugins_paused`, but each extension keeps its on/off switch, settings and data. A banner says since when and by whom, extensions read *Paused*, and **Resume** starts again exactly the ones switched on. The pause survives a restart and holds the Python bots too. `GET /api/plugins/pause` (members and up) and `PUT /api/plugins/pause` (administrators, idempotent, audited `plugins.paused` and `plugins.resumed`); `/api/health` reports `plugins_disabled`, `plugins_disabled_source: "paused"` and `plugins_paused_since`, with `MESHCORE_DISABLE_PLUGINS` and the platform taking precedence (migration 120). The user guide compares it with switching one extension off and with the host lock. English and French.
- **"Keep me signed in" on the login page.** The sign-in form has a **Keep me signed in on this device** box, unchecked by default. Checked, the session lasts 30 days, sliding and survives closing the browser. Unchecked, the cookie has no `Max-Age`, so the browser drops it when it quits, and the session also ends on the server after 12 hours without activity (never longer than the 30 days). `POST /api/auth/login` and `POST /api/auth/webauthn/login/verify` take `remember` (default `false`), carried through the second step of a two-factor sign-in (migration 117 adds `sessions.remember`; existing sessions keep the long lifetime). On the login page, a browser that supports passkeys on an address where the server does not offer them now says why the passkey button is missing. English and French.
- **Event rows in the Feed.** The *Events* chip of the Feed, until now unavailable, is on by default and adds a line when the radio goes offline (after the *radio offline* delay of the Notifications rules, read by administrators; every other session uses the default minute; a shorter flap and the reboots Meshloom starts itself add nothing) and comes back online with how long it was away, when a new contact is heard (unknown and room types excluded, like the notifications), when a contact changes its name and when a channel is discovered. They are built from the live frames the browser already receives (no new route, nothing stored), use the Notifications event ids, are ordered with the messages on the server's clock, follow the session's rights (the *Discovered channels* link needs the right to send, the radio link the administrator role, none in display mode) and are kept for the session only, which the Feed says. A browser that stored its filters before this change keeps *Events* off until the chip is ticked. English and French.
- **Notification limits, grouping and a global cap.** Every notification channel now has real limits per minute, per hour and per day (Telegram 15 a minute and 200 an hour, Discord 30 and 600, e-mail 6, 60 and 200 a day, webhook 120 a minute, Web Push 60, ntfy.sh 10, 30 and 200 a day; edit them per channel), and the ntfy.sh daily default is finally **enforced** instead of only shown. A full minute or hour limit no longer drops anything: the notifications wait one minute and go out as **one summary** ("+14 messages: 12 in #paris, 2 DMs") that only counts, never carrying a message text, a sender or the name of a private channel. Urgent events (radio offline, telemetry alert) take a token like any other but pass a full limit once per event, so a flapping sensor cannot get round it. A reached daily limit stops the channel until the next UTC day (the count survives a restart) and is logged as *quota exceeded*; over all channels, Meshloom sends at most 600 notifications an hour (`limits.global_per_hour` in the rules), logs the rest as *rate limited* and reports `notifications_capped: true` in `/api/health`. Dedup survives a restart too: a new contact notifies once a day, a radio outage once, a message once. English and French user guide updated.
- **A notifications guide, in English and French.** The new [Notifications](docs/user/en/deep/notifications.md) page (Go deeper) explains channels and their types, what leaves your machine and the text policy of each channel, destinations on your network, events and the rules table, mentions, the conversation bell and exceptions, Do not disturb, mute and block lists, backups without secrets, the test button and its error codes, and troubleshooting. The Push page keeps only what is specific to browsers (HTTPS, the VAPID contact address, devices); the Fanout, Security, Variables and Home Assistant pages point to the new one, and the Fanout page now says which of its two webhooks and which Apprise is which.
- **The conversation bell lists your notification channels.** In the header of a conversation (administrators only: the kiosk, viewer and member roles have no bell), the bell now shows one tick box per enabled channel, with its icon, name and a status dot, instead of the fixed Push, Email, Webhook and Apprise boxes. A box shows whether the channel notifies this conversation and why (*default*, *on here*, *off here*); changing it makes an exception for that conversation, and going back to what the default says removes it. Switching **Web Push** on subscribes the browser first. The menu also has the **Do not disturb** shortcut (1 hour, 8 hours, until tomorrow 8:00, resume) and a link to the channel settings, and the same menu is under the three dots of a conversation in the list. On the **Alerts** page, where telemetry alerts are sent is now a button per channel, the same row as in the Notifications table. The browser mirror of the conversation policy (`notificationPolicy.ts`) is tested against the same cases as the server. English and French.
- **Extensions in the backup.** The full backup now has an *Extensions* part, **without any secret**: an extension's secret settings and the data it stored never leave Meshloom (the file only names the secret settings that were set). A local extension comes back whole with its code; any other extension comes back as an entry **to reinstall** that cannot start (`409 plugin_reinstall_required`), except the extensions Meshloom ships, which it brings back by itself. Every restored extension comes back **switched off** and asks for a review before it starts; one already installed here is left as it is, and a bad entry is skipped with a warning. Backups without this part restore as before. English and French.
- **The restore summary names whose account each push subscription goes to.** Before a restore, each push address listed in the summary now says the account it goes back to (same user name, from the file's accounts or this Meshloom's), or *the first administrator* when none matches (`push_targets[].owner` in `POST /settings/backup/inspect`). English and French.
- **Notification channels and rules in the backup.** The full backup now has a *Notifications* part: every channel (type, name, options, limits) and the rules, **without any secret**: tokens, passwords and addresses that carry a token never leave Meshloom, and the e-mail, webhook and Apprise destinations of the settings are no longer written with their secrets in the full backup either (a restore keeps the ones already stored). After a restore, a channel that lost any secret, required or optional, comes back **switched off** and marked *Secret missing after a restore* (English and French); it cannot be switched on until each secret is typed again or cleared on purpose. Restored rules are checked like the rules API does and malformed entries are left out; a channel that breaks the rules of a new one (second Web Push, invalid id, missing required field) is skipped with a warning. Meshloom never sends with an empty secret. A channel that already exists with the same address keeps its secret, and a pause is not restored. A backup made by 4.18.x restores into the same channels and rules as the upgrade does. The support snapshot (`GET /api/debug`) lists the channels with the names of their secrets that are set, never the values.
- **Stricter checks on the Telegram, Gotify and Home Assistant presets.** Preset fields accept ASCII digits and letters only (a token written with Arabic-Indic digits used to pass and fail only at send time); a Gotify path containing `..` or `%` is refused; and a Home Assistant address with a path (`https://host/ha`, behind a reverse proxy on a sub-path) is now refused with the field flagged and its help text saying so, instead of silently losing the path. English and French.
- **Telegram, Gotify and Home Assistant channels.** **Settings → Notifications → Add a channel** now offers three **presets**: forms with named fields for services Meshloom reaches through Apprise. **Telegram** (bot token, write-only; chat id, required; optional topic; a third party, so private text stays home by default; 15 messages a minute and 200 an hour, and Telegram's own *too many requests* delay is honoured), **Gotify** (server and application token, write-only; priority; 60 a minute) and **Home Assistant** (address, long-lived access token, write-only, and the notify service of your phone's Companion app; 60 a minute). Each form explains where to find its values and links to the service's documentation; the Apprise address is built from the fields when a message is sent, checked like any other destination (a server on your home network asks for **Allow a private-network destination**) and never stored. A preset is data: a new service needs no code. `GET /api/notifications/types` lists the presets with `preset: true`, `base_type: "apprise"` and a JSON Schema built from their fields. English and French.
- **Mention notifications.** A channel message that contains `@[your radio name]` (any case), or your radio's name as a whole word when **Match whole words only** is ticked, now notifies the channels ticked on the **Mentions** row of **Settings → Notifications**, as "Mention in #paris by Léa", instead of the ordinary channel notification on those channels (never both). It is never urgent, the channel mute, block lists and Do not disturb apply, and on a private-key channel it only reaches channels that are not a third party (a webhook always counts as one) or that you allowed for that conversation (anyone in range can forge a mention). New and existing installs get the Mentions row filled with the channels of direct messages (migration 115). English and French.
- **ntfy channels.** **Settings → Notifications → Add a channel → ntfy** sends notifications to an [ntfy](https://ntfy.sh) topic (ntfy.sh or your own server) with Meshloom's own client instead of an Apprise URL: the message is published as JSON, so accents in titles work and the topic, which is the only secret on ntfy.sh, never appears in a URL or a log. Fields: server, topic (write-only, required), an access token or a user name and password (write-only), the priority of ordinary events (3) and of urgent ones such as *radio offline* (5), tags, the address of this Meshloom (a tap on the notification opens the conversation), Markdown, and the certificate check (it can only be turned off for a server on your private network, with the private-network option). Errors are explicit: wrong credentials, quota reached, rate limit (ntfy's `Retry-After` is honoured), certificate failure. ntfy.sh channels are treated as a third party (the text of direct messages and private channels stays home by default) and show a default limit of 10 messages a minute, 30 an hour and 200 a day (ntfy.sh's free plan allows 250 a day); a self-hosted server has no daily limit. The limits are enforced by the notification limiter (see *Notification limits, grouping and a global cap*), and a quota that ntfy.sh itself reports as spent is shown as *quota reached*. The topic is public by obscurity: use a long random one (for example `openssl rand -base64 18 | tr '+/' '-_'`) or your own server. A new integration test publishes to a real ntfy container in CI.
- **Discord channel.** **Settings → Notifications → Add a channel → Discord** posts to a Discord webhook (`discord.com` or `discordapp.com` only; the URL is write-only, with an optional username and no avatar override). Every request carries `allowed_mentions: {"parse": []}`, so `@everyone`, `@here`, users and roles are never pinged, the text is Markdown-escaped, link previews are off, and Discord's rate-limit headers and `429 Retry-After` are honoured. The Markdown escaping (and mention neutralising) now lives in one shared module for every Markdown channel, Apprise in Markdown format included. English and French.
- **Notification rules in Settings.** **Settings → Notifications** now has **Do not disturb** (1 hour, 8 hours or until tomorrow 8:00, with the time left and **Resume now**), an events × channels table (events grouped as Messages, Nodes, Radio, Telemetry and Meshloom; one tick per channel, an **Always deliver** column that passes the pause, sub-rows per node type for new nodes, the radio offline delay and the whole-word mention tick next to their rows, **Channel changed** locked on Web Push, switched-off channels greyed) and conversation exceptions with a button per channel (inherit, on, off) and a remove button. The table scrolls inside its box on a phone, keeps its first column in view and every box has a label. It replaces the fixed Push, Email, Webhook and Apprise columns and uses the rules API. English and French.
- **Quiet hours and a summary after the pause.** **Settings → Notifications** has a **Quiet hours** group (switch, start and end time, days, summary option; times follow the machine's clock) and an **Exempt from quiet hours** box on every channel card. During the window non-urgent notifications are held, then one summary per channel says how many came in. **Do not disturb** now does the same when it ends: what it held is counted and summed up, instead of being lost. A summary that a rate limit refused is tried again for ten minutes, one held by a channel's own pause waits for the pause to end (no time limit), and the option is now labelled *Send a summary after quiet hours and after a pause* since it governs both, the same event arriving twice is counted once, and the digest timer starts with the app. The webhook imported from 4.18.x is not exempt (quiet hours did not exist then): tick its box if it must keep receiving. English and French.
- **Notification channels in Settings.** **Settings → Notifications → Channels** replaces the fixed e-mail, webhook and Apprise forms with a card per channel (type, status dot, last error, on/off switch, **Test**, **Edit**, **Delete**); the Web Push card holds this browser's subscription, the devices and the VAPID subject, and says when Web Push is unavailable over HTTP. **Add a channel** picks a type or a preset, shows the form the server describes (required fields starred, server errors under the field, secrets never shown and kept unless replaced or cleared), sends a test **before** saving, then asks **What to receive here?** (Like Push, Alerts only or Everything). A cloud service shows a privacy note and a message-text choice (public channels only by default). Your existing e-mail, webhook and Apprise destinations (`legacy:*`) stay editable and removable from their card with the 4.18.x forms, until the new rules deliver (the channel routes answer `409 legacy_managed` for them). English and French.
- **Extension settings and local extensions.** In **Settings → Extensions**, an extension's drawer now has a **Settings** form generated from the extension's own description of its settings: required fields are starred, the server's validation errors appear under the field, and a secret setting (an API key, a password) is never shown, even after saving: the form says a value is set and offers **Replace** (empty keeps it) and **Clear**; only what you change is sent. An extension waiting for required settings has a **Go to the settings** button. **New local extension** creates a Python extension from a template; its drawer has the code editor (saved after a syntax check, with the scan's findings listed by line and the servers named in the code offered with an **Allow** button), editable events and permissions, **Check** (errors and warnings by line, nothing is run) and **Test with a message** (a made-up message through the running extension, showing its answers and log; nothing is sent to the radio). The form is a shared component that the notification channels will reuse. `PATCH /api/plugins/{id}` accepts `hooks` for a local extension, and `PUT source` and `POST check` return the `https://` hosts found in the code (`http_hosts`).
- **Extensions in Settings.** The new **Settings → Extensions** section (administrators) lists the installed extensions with their status, the reason one needs attention, and whether it is bundled; turn one on or off, reorder them, add a bundled extension after reading what it may do (permissions in plain words, the servers it talks to, its privacy declaration, and a warning when it can read private messages and reach the internet), and open a drawer to lower its permissions, choose the conversations it may read, lower its sending limits, read its log, restart it, copy a diagnostic or uninstall it (keeping its data or not). Revoked and incompatible extensions cannot be turned on, and secret settings are never shown. The top of the page shows the isolation level in force. `GET /api/plugins/bundled` now also returns each package's `privacy` declaration.
- **Display mode for the Feed (wall screens and kiosks).** The **Display mode** button in the Feed's header, or `display=1` in the address (also as the **Start page** or in `MESHLOOM_KIOSK_URL`), turns the Feed into a chrome-free screen: text 25 % larger through the existing font scale, no rail, bottom bar, header, chips or Nodes facet, the newest message always followed, day separators, a discreet clock, no blur or shadow, and full screen when the browser allows it (silently skipped otherwise; under Home Assistant it needs `allowfullscreen` on Home Assistant's frame). Direct messages are hidden (the first request carries `type=CHAN`) and images and GIFs are not loaded, whatever the address says (`types=chan,priv` is ignored in the mode); rows are read-only and not focusable, and **Exit** is the only control. It is a display choice, not an access control: the browser still receives every frame, and the `kiosk` role is what refuses direct messages. **Exit** restores the previous filters; the remembered filters, the font scale and the reactions preference are never rewritten. `docs/user/{en,fr}/rpi.md` gets a Display mode section.
- **Notifications API.** `/api/notifications` (administrators) manages the notification channels: `GET /types` serves each channel type's JSON Schema (secret fields are `writeOnly`), `GET`/`POST /channels` and `GET`/`PATCH`/`DELETE /channels/{id}` list, create, change and delete channels (secrets read `********`; omit them, send `null` or `********` to keep them, `""` clears one; the type cannot change; `builtin:push` cannot be deleted), `GET`/`PATCH /rules` and `PUT /rules/conversations/{key}` read and change which channels each event and conversation reaches, the global pause (`paused_until`: everything stops except urgent and *always deliver* events), the radio offline delay and the host time zone (read-only), and `GET /deliveries` and `GET /events` give the journal and the event catalogue. `POST /channels/test` tests a configuration before saving it (nothing is stored) and `POST /channels/{id}/test` tests a saved channel, at most once every 5 seconds per channel (`429 rate_limited`); both go through the same network checks as a save. Errors are `{code, params}` in French and English, never a host, an address or an exception text. Every creation, change of destination, enabling, disabling or deletion of a channel is written to the audit log, sent to administrators as a `notification_channel_changed` live event and notified to Web Push (which cannot be unchecked) and to every other enabled channel. Changes to the rules and the global pause are audited too. The destinations of the `legacy:*` channels are still set through Settings (the new routes answer `409 legacy_managed` for a change of destination and for deleting those channels). `GET`/`PATCH /api/push/preferences`, `PUT /api/push/preferences/conversations/{key}`, `notification_destinations` in `PATCH /api/settings` and `POST /api/settings/notification-destinations/test` still work but are **deprecated** (they answer a `Deprecation` header) and will be removed in 5.2.0; turning on a medium for a conversation when its destination is not configured now answers `400 channel_missing`, and the destination test now answers the codes above instead of `notification_test_failed`.
- **The Feed under the `kiosk` role.** A session without the right to read direct messages (the `kiosk` role, detected by capability, not by role name) gets a Feed with no **Direct messages** chip: the first request carries `type=CHAN`, `types=priv` in the address and a remembered direct-messages filter are ignored (and not rewritten in storage), a direct-message frame that reaches the browser is dropped before it enters the Feed, and a refused list (403) shows the normal empty state with an explanation instead of an error. Control and muted or pending channels stay readable. `docs/user/{en,fr}/around.md` and `rpi.md` say so.
- **Start page.** **Settings → Navigation → Start page** (administrators) chooses where Meshloom opens when the address has no `#` part: the conversations (default, unchanged), the map or any tool, including the Feed and its Control preset. It is stored with the instance (`ui_preferences.start_view`, refused with 400 when it is not a known tool), so it applies to every session and every browser, the kiosk screen included. A link to a conversation or a tool still wins, and so does **Reopen the last viewed** on a device that turned it on and has something to reopen. Saving the navigation rail no longer drops the other interface preferences.
- **Feed filters in the address.** `#feed?src=messages,control,events&types=chan,priv&dir=in,out&muted=1&pending=1&node=<public key>&display=1` (and `#control?display=1`) pins the Feed's filters in the address, so a bookmark or a kiosk URL opens the same view without relying on the browser's storage. The address wins over the filters remembered in the browser, which win over the defaults; it is applied before the first request, and it follows your changes to the chips (without adding history entries). A private channel's key is its access and bookmarks are synced by browsers: the address never carries a channel key, only source and type names and a node's public key, and anything unknown or malformed in it is ignored. The start page setting accepts the same form (`feed?...`, `control?...`), validated by the server against an allowlist (400 otherwise).
- **Kiosk images get a read-only `kiosk` account and show the installation code.** A Raspberry Pi kiosk image now marks itself with `/var/lib/meshloom/kiosk` (the package writes it on kiosk images flashed before 5.0), creates a password-less `kiosk` account at first start and shows **Administrator account not created yet** with the installation code on its own screen until the administrator is created from another device. The package also ships the kiosk launcher at the same path, which now waits on `/api/auth/session`. The wall screen now signs itself in (next entry).
- **The kiosk screen signs itself in with a device token.** On a kiosk image, Meshloom writes a read-only device token of the `kiosk` account to `/var/lib/meshloom-kiosk/kiosk.token`, readable only by the Pi's desktop user, and the launcher trades it for a session through a one-time link that works only from the Pi itself (`POST /api/auth/kiosk-bootstrap`, `GET /api/auth/kiosk-session`): the token never appears in an address or in the list of running programs, and no trusted-network rule is needed. While no administrator exists the screen shows the setup page with its code, then opens Meshloom by itself once the administrator is created from another device. Revoking **Kiosk screen** in **API and device tokens** signs the screen out and writes a new token at once, and the screen signs in again with it within seconds; disabling the `kiosk` account keeps the screen signed out. The token works only for that sign-in on the Pi itself, and both routes are rate limited. A kiosk image flashed before 5.0 signs in after one reboot following the update.
- **Two-factor authentication (TOTP).** Any account with a password can add a 6-digit code from an authenticator app: `POST /api/auth/2fa/enrol` (with the password) returns the secret and its `otpauth://` link once, `POST /api/auth/2fa/confirm` turns it on with the first code and returns ten single-use recovery codes. Signing in then takes two steps: `POST /api/auth/login` answers a pending sign-in instead of a session, and `POST /api/auth/login/2fa` exchanges it with a code (or a recovery code) for the session. A code works once, wrong codes count like wrong passwords, Basic auth stops working for such an account, and `POST /api/auth/2fa/disable` / `POST /api/auth/2fa/recovery` need the password and a code. Administrators can require it for administrators with `require_2fa_for_admins` (`PATCH /api/auth/settings`, off by default); `meshloom-admin disable-2fa <user>` removes a lost second factor from the machine. The full backup carries the secret and the recovery code hashes only with **Include accounts**.
- **Two-factor authentication in the interface.** **Settings → Users & Access → Two-factor authentication** sets it up: confirm your password, scan the QR code (drawn in your browser, the key never goes to another service) or type the key, enter the first code, then copy or download your ten recovery codes, shown once with a warning. The same section generates new recovery codes and turns it off, both with your password and a code. Signing in shows a second step for the code (or a recovery code) with clear errors, and sends you back to the password if the pending sign-in expired. An administrator can tick **Require two-factor authentication for administrators** there; an administrator who has not set it up then sees only the setup page. Under Home Assistant, or for an account without a password, the section explains why it does not apply.
- **Passkeys (server side).** An account with a password can register passkeys (`POST /api/auth/webauthn/register/options` with the password, and a code when two-factor authentication is on, then `.../register/verify`), list them and remove them with the password (`GET`/`DELETE /api/auth/webauthn/credentials`), and sign in without a password (`POST /api/auth/webauthn/login/options` and `.../login/verify`, the device must verify the user; this replaces both the password and the two-factor code). With two-factor authentication on, a passkey can replace the code at `POST /api/auth/login/2fa`. Passkeys work only over https on a declared public address, through Meshloom Remote or on `localhost` (`GET /api/auth/session` says `methods.passkey`); plain http on the local network and Home Assistant ingress are refused. Challenges work once within 5 minutes, failures count like wrong passwords, a counter that goes back is refused, and `meshloom-admin remove-passkeys <user>` removes a user's passkeys from the machine. The full backup carries them only with **Include accounts**.
- **Passkeys in the interface.** **Settings → Users & Access → Passkeys** lists your passkeys (name, added, last used), adds one (a name, your password and, with two-factor authentication on, a code, then your browser's prompt) and removes one with your password; it warns that a recovery code is used up as soon as you submit, even if you cancel the browser's prompt. The sign-in page gets **Sign in with a passkey**, shown only when the address supports passkeys and the browser can use them (hidden with an explanation on plain http), and the second step offers **Use a passkey** for accounts that have one. The WebAuthn browser library (`@simplewebauthn/browser`) loads only when a passkey action starts, and cancelled, refused and already-registered prompts get their own messages. The server now logs a warning when it refuses passkey sign-ins because its table of live challenges is full.
- **API and device tokens.** A script, a Home Assistant sensor or a phone app can now sign in with its own token instead of a password: `POST /api/auth/tokens` (members and administrators, for their own account) returns a `mlt_…` token once, sent as `Authorization: Bearer` on every request and on the live connection (`/api/ws`). Each token has scopes (`read`, `read_private`, `send`, `repeater_admin`, `admin`) that never exceed its account's role, an optional expiry and its last use; `DELETE /api/auth/tokens/<id>` revokes it at once and closes its open connection. Meshloom stores only a fingerprint of each token, ignores a token put in the address, refuses tokens through Home Assistant ingress and writes creations, revocations and uses of a revoked or expired token to the audit log. The full backup carries them with the accounts.
- **Device pairing codes.** An administrator can create a single-use pairing code (`POST /api/auth/pairings`: a label, the scopes the device gets, a lifetime of at most 10 minutes) and its QR text (`meshloom://pair?d=…`, carrying the code, the instance's https public addresses and, when it is a private network one, the local address the administrator used). A phone app such as meshenger sends the code to `POST /api/auth/pair` with a device name and gets its own device token, with exactly those scopes, attached to the account that created the code. A code works once: a second use, an expired or a revoked code answer `auth_pairing_expired`. Meshloom keeps only a fingerprint of each code, slows down the client that sends wrong codes (never everyone), refuses pairing through Home Assistant's ingress and writes creations, revocations and uses to the audit log. Contract for app developers: `docs/dev/device-pairing.md`.
- **Pair a device from Settings.** **Settings → Users & Access → Pair a device** (administrators) creates a pairing code from a device name, the scopes it gets and a lifetime of 1 to 10 minutes, then shows its QR code (drawn in your browser, loaded only when needed) with a live countdown. The QR code disappears when the code expires, **Cancel the code** stops it at once, and the panel lists the pending codes with a cancel button. The dialog explains what the QR code is for (enrolling meshenger or another device) and warns that it must not be shared. English and French.
- **Tokens in Settings.** **Settings → Users & Access** has a new **API and device tokens** section for members and administrators: create a token (name, the scopes your role allows, an expiry or none), copy it from a one-time screen that warns it will not be shown again, see each token's scopes, creation, last use and expiry (an administrator also sees whose it is), and revoke one after a confirmation. Signed in through Home Assistant, the section explains that tokens do not work through the add-on's ingress.
- **Trusted networks.** In **Settings → Users & Access → Trusted networks** an administrator can sign the devices of a chosen network in as a chosen account, without a login (empty by default; `trusted_networks` in `GET`/`PATCH /api/auth/settings`). A rule never applies to a request that came through a reverse proxy or a tunnel (forwarding headers, or a peer listed as a trusted proxy), Meshloom Remote or Home Assistant ingress, nor from loopback outside the add-on, and only when the browser uses an IP address or a local name. Rules that overlap a trusted proxy are refused. Each automatic sign-in is written to the audit log once per device and per day. A red warning shows when a rule signs in an administrator, and the new Access security notice at the top of Users & Access flags an administrator rule while plugins run with reduced isolation. It also flags a rule whose network is public or as wide as allowed (a `/8` IPv4 or `/16` IPv6 range). A rule for an account that must change its password is refused until the password is changed.
- **Users & Access in Settings.** The new **Users & Access** section lets an administrator list the accounts (role, local or Home Assistant, disabled), create one (member, viewer, kiosk), change a role, disable or enable, reset a password (with a change forced at the next sign-in), sign out everywhere, delete, and choose the role of new Home Assistant users. Every user changes their display name and password there, sees their sessions (device, address, last seen) and revokes one or all the others. Errors (last administrator, refused password, too many attempts, busy server) are explained in French and English.

- **Audit log in Settings.** **Users & Access** now ends with an **Audit log** for administrators: who did what, newest first, with the time, the account (or address) that acted, the action, what it targeted, the way in and the outcome, all in French and English. Filter by action family, action, outcome, account and dates; load older entries page by page. Filters apply to the entries already loaded.

- **Members, viewers and kiosks now get the instance's theme and blocked lists.** A new read-only route, `GET /api/settings/view`, open to every role, returns only what the interface needs to start: the theme and rail, the blocked keys and names, the regions and the unread markers (the markers of direct conversations are left out for a kiosk). Nothing administrative or secret is in it, and `GET /api/settings` stays administrator-only. Until now these roles ran on the defaults and still saw messages from contacts an administrator had blocked.

- **The allowed-origins list is checked again when it is read.** An entry stored outside the settings page (manual SQL, a restore) that is `*`, `null`, has a path or is not `http(s)` is ignored and logged, so it can never let every site in.
- **Oversized messages are refused by the server.** Until now only the web composer checked the size, so bots and API clients could send messages the radio cannot carry. The server now applies the same limit to direct messages (156 bytes) and channel messages (156 bytes minus your node name and the `: ` separator), counted in bytes, on every path including resends. An oversized message is refused with a clear error and nothing is sent or stored. Known limit: after renaming your node to a longer name, an old message that fitted may be refused on resend.
- **The full backup can carry parts added by other features.** Features such as accounts or plugins can now add their own part to the full backup. Before you restore, the summary lists these parts and marks the ones holding secrets; a backup holding such parts asks to be encrypted. A part your Meshloom does not know, or one made by a newer version, is left aside and the rest is restored; a part that fails is left as it was. Backups from 4.18 and earlier still restore. Restore a full backup on 5.0 or later only: 4.18 restores just its contacts, channels, settings and groups, without saying so.
- **The full backup carries the accounts.** **Settings → Database** has a new **Include accounts** box, checked by default, with a warning that the file then contains password hashes and two-factor secrets (uncheck it before sending a backup to someone). Restoring such a file on a new machine brings the same accounts and passwords back; it replaces the current accounts and signs every session out. A restore never leaves Meshloom without an enabled administrator who can sign in on it (a password account, or a Home Assistant user on the add-on): a file without one, or without accounts, keeps the current accounts. Login sessions, the audit log, pairing codes and the Meshloom Remote identity are never in the file and are ignored if a file carries them. A trusted network whose account is a different person after the restore is removed.
- **Two new filters on `GET /api/messages`.** `outgoing=true|false` lists only sent or only received messages, and `since_id=N` returns the messages stored after id N in id order, historical ones included, so an API client can catch up without gaps. `since_id` cannot be combined with `before`, `after` or `offset`.

- **Radio offline, back online and Meshloom started notifications.** Meshloom now tells you when the radio has been unreachable for more than 60 seconds (urgent, one notification per outage), when it is back with how long it was down, and each time Meshloom starts (version, radio connected or not). Push is on by default; e-mail and webhook can be ticked in **Settings → Notifications**, where the delay is also set (0 notifies at once). Nothing is sent for a reboot Meshloom started itself, while the connection is paused, before the radio has connected once after a start, or during the delay above right after a start, so an update does not raise a false alarm. These notifications cannot reach you while Meshloom itself is down; “Meshloom started” shows an unexpected restart.
- **A Feed page shows every message in one timeline.** The new **Feed** tool (**Fil** in French; rail, Tools, search palette or `#feed`) lists the messages of all your channels and direct messages, newest at the bottom, live as they arrive. It follows the newest message while you stay at the bottom; scroll up and a **Follow** button counts what arrived since and takes you back. Scrolling up loads older messages from the history, 200 at a time, until the beginning. This first version shows the time, the conversation and the text; names, filters and the Control journal folding in come next. The Control journal is unchanged, and a rail you arranged before is not given the new entry: add it from the rail menu.
- **The Feed shows names, day separators and filter chips.** Each message shows its channel or contact name, the sender (hover for the start of their key, to tell two people with the same name apart), the time, the region and a *sent* marker for your own messages, with a separator at the start of each day. Chips above the timeline pick what to show: Channels, Direct messages, Received, Sent, and Muted or Pending channels, which are hidden by default with a count of what they hide. A timed mute that has run out shows its messages again within a minute. Your choice is remembered in this browser only (a second tab picks it up on reload). When the filters leave a page almost empty, the Feed loads older pages by itself, at most five at a time. Messages from a pending channel never load outside media such as GIFs.
- **The Feed stays complete and shows where you left off.** Messages that land in the middle of the timeline (a historical decryption, a message you sent) take their place without moving what you are reading; sending a message brings you back to the bottom, while old messages that only just got decrypted never count as new. Acknowledgements, deletions and renames update the rows in place. After a reconnection, or once a historical decryption finishes, the Feed fetches what it missed from the server (a deletion made while you were offline is only noticed on reload). A line **New since your last visit** marks the first message that arrived after you last had the Feed open, per browser.
- **Feed messages can be opened and acted on.** Click a message (or press Enter on it) to open its conversation at that message, highlighted; the Feed is left, and the back button of a phone returns to it. The channel or contact name opens the conversation at its latest message, and a sender whose full key is known opens their contact details. The **...** button on each message (a long press on a touch screen, a right click or the menu key elsewhere) offers *Open in conversation*, *Copy text* and *Contact details*; *Copy text* is hidden when the browser offers no clipboard, which is the case over plain HTTP on a local network.
- **The Feed shows control traffic and a Nodes facet.** The **Control** chip is now on by default: Request, Response, anonymous request and group data packets appear in the timeline between the messages, in time order, with their type, from → to, the decrypted group data or the locked envelope (destination and source), and a click opens the packet inspector. A **Paired** badge on a request or response scrolls to its twin when it is on screen. Unlike the raw packet buffer, these rows are kept when the connection drops and comes back, and are not added twice. The **Nodes** facet (a column on a computer, a button opening a sheet on a phone) lists repeaters and rooms, channels, packets addressed to us and unresolved ones, with counts; picking one narrows the control rows to it, and a channel also narrows the messages to that channel. Requests and anonymous requests of other nodes are still never decrypted.
- **Apprise as a fourth notification medium.** Next to push, e-mail and webhook, **Settings → Notifications** now has an **Apprise** column and destination: ntfy, Telegram, Discord, Home Assistant and the other reviewed Apprise services, one URL per line, with four examples and the Apprise wiki link. By default only the text of Public and `#` channels leaves; direct messages and private channels are sent as a title only ("Message from Léa"), and you can choose *Always* or *Never*. Mentions such as `@everyone` are neutralised, and the Markdown format escapes the message text. The URLs are write-only secrets (shown as `********`, left out of the JSON backup) and are checked at save and before each send; a private network needs the usual opt-in. Each URL is sent and retried on its own (5 s, then 30 s, only when the service is down or asks to slow down), at most one send every 2 seconds, with a single "+N events" past 20 waiting and new contacts capped at 10 an hour. Blocked contacts and muted channels never reach Apprise, nor the Apprise fanout. Any change of a notification destination now sends a push notification "Notification destination changed" to every subscribed browser. The last failure is in `/api/health` (`notification_statuses`).
- **`meshloom-admin`, a command to recover access from the machine.** It resets a password, creates an administrator, issues the installation code, lists, disables and enables users and signs sessions out, by editing the database directly, so a running Meshloom needs no restart. It ships in the Linux package (`sudo meshloom-admin`) and in the Docker image and the Home Assistant add-on. On the package it refuses an `/etc/meshloom/meshloom.env` that anyone but root could change. The exact command for each way of installing is in the new "Recover access" page.
- **Settings for link and file sharing.** `GET`/`PATCH /api/settings` now carry `share_enabled` (the consent to the sharing service, off by default), `share_shorten_mode` (`auto`, `always` or `never`; `auto` by default) and `share_preview_policy` (`always`, `private` or `never`; `private` by default); an unknown value is refused with a 422. A backup restores the two behaviours but never the consent, and the instance identity seed created later never leaves the server.
- **Your acceptance of the sharing terms is recorded.** Turning on link and file sharing through the consent window now stores, on this Meshloom, which version of the terms you accepted and when (`GET /api/share/status` returns `terms_version`, `terms_accepted_version`, `terms_accepted_at` and `terms_stale`). If the terms change in a later version, sharing stays off until an administrator accepts the new ones, and the sharing section says so. `PATCH /api/settings` refuses `share_enabled: true` without an acceptance of the current terms (409 `share_terms_required`). A backup never carries the acceptance. Meshloom installs that already had sharing on in a pre-release are asked once more.
- **The share service client is in place, off by default.** Meshloom can now talk to the link and file sharing service (`meshl.ink`) once an administrator turns sharing on. While sharing is off, Meshloom makes no connection to it at all, not even a DNS lookup. When it is on, Meshloom contacts only that host, over HTTPS, refuses to follow it anywhere else, and never sends your channel keys or radio key. It spares the service: link previews and downloads are capped per minute, a missing or expired link is remembered for 10 minutes, three failures in a row pause every call for 5 minutes, and a ban stops sending for 12 hours (or until a share setting changes or Meshloom restarts). Turning sharing off stops the requests already on their way at once. Errors come back with a translated reason (service unavailable, quota reached, link refused...); an error page from the service's host counts as "unavailable".
- **Store egress switch, off by default (groundwork).** `app_settings.store_online` is now readable and writable through `GET`/`PATCH /api/settings` (no interface yet) and `GET /api/store/status` (administrators) tells, without making a request, whether the Store may go online, the exact hosts it may contact (`github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com`, `store.meshloom.app`), what they see (IP address, `Meshloom/<version>`), the 6-hour refresh cadence and what is missed while it is off. While it is off nothing leaves for the Store, whether Community is on or not; turning it off cancels requests in progress. A backup never carries it.
- **Local routes for short links.** `POST /api/share/links` shortens an `http(s)` URL through the share service (members and administrators), `GET /api/share/status` reports whether sharing is on, the service host, whether the instance key exists, whether direct-message encryption is available and whether the service banned this Meshloom (no request is made), and `GET /api/share/{id}/meta` tells what a link is without following it (kind, target host, expiry; a good answer is remembered for 10 minutes). A URL with a password, an IP address or a local host name is refused before anything is sent. While sharing is off they answer `share_disabled` and make no connection. A write that the browser marks as coming from another site is refused (`share_cross_site`).
- **Groundwork for plugins.** The database now has tables for plugin instances, their storage and their secret settings, and a Store consent setting that is off. Nothing uses them yet, and existing bots are not changed. Plugin secret settings will never be in a backup or in the database download.
- **Plugins run in supervised processes.** Each enabled plugin instance runs in its own process with time and CPU budgets: an event that takes more than 30 seconds is cancelled, and a plugin that ignores the cancel is stopped and restarted after a growing pause; after five failures in a row it stays stopped until you restart it. A plugin that sends Meshloom an oversized message is stopped and restarted the same way. Events waiting for a busy plugin are capped at 100 (the oldest are dropped and counted). The number of running plugins is capped from the machine's memory. `MESHCORE_DISABLE_PLUGINS=true` keeps every plugin stopped; on Windows the plugin system is off. Nothing installs plugins yet, and existing bots are not changed.
- **`python -m meshloom_plugin.dev check <dir>` checks a plugin before you ship it.** It runs the same manifest and package rules as the server (same messages), a static review of the plugin's source, then starts the plugin in the sandbox, sends it `plugin.init` and one test message, and prints what it tried to send (nothing leaves your machine). It exits non-zero on any error and prints JSON with `--json`, for CI.
- **Plugins run behind operating-system walls.** On Linux each plugin process runs in a bubblewrap sandbox (no database, no network, no view of the server; the Linux package now recommends `bubblewrap` and the Raspberry Pi images include it), and in the Docker image and the Home Assistant add-on each plugin copy runs under an account of its own (see Security). At that level, Meshloom makes its data folder `0711` and the files in it `0600` at startup, so open the database from the host as root. Where neither works, plugins run with "hooks only": the server log warns at every start. If the isolation cannot be set up, no plugin starts and `/api/health` says `plugins_disabled_source: "isolation"`. The level is in `/api/health` (`plugins_isolation`). Bots of 4.18 are unchanged.
- **Plugins can only do what you allowed.** A plugin asks Meshloom for everything it does, and Meshloom checks each request against the permissions it was granted: reading messages (public channels only by default, private channels, direct messages and your own sent messages only when allowed), sending (by default only replies in the conversation it answers), contacts, channels, storage (5 MB) and timers (at least 10 seconds). A plugin sends at most 30 messages per hour and 200 per day by default (at most 60 and 500), counted across restarts, and every automatic message, bot or plugin, waits 2 seconds after the previous one. A plugin never gets its own messages back unless it may read your sent messages. Nothing installs plugins yet, and existing bots are not changed.
- **Plugins together can never flood the mesh.** On top of each plugin's own limits, Meshloom now caps all plugins together at 120 messages per hour, each channel at 20 per hour and each contact at 10 per hour (they can be lowered later, never raised; the control comes with the plugin settings screen), and cuts loops between auto-responders: a plugin copy answers the same sender at most once every 30 seconds and ignores a message that arrived less than 5 seconds after its own send to the same conversation. Two echo plugins, even on two different Meshloom, are silenced after a few messages; a refused send is logged with the guard that fired. The counters survive a restart. Nothing installs plugins yet, and existing bots are not changed.
- **Plugins reach only the internet servers you allowed.** A plugin with the internet permission asks Meshloom to make each request, and Meshloom sends it only to the exact server names the plugin lists and you accepted, over HTTPS, never to your local network (whatever the name resolves to), and follows at most three redirects, each to another listed name. A plugin's passwords or API keys never follow a redirect to another server, and the request carries no cookie and nothing of Meshloom's own. Answers are cut at 1 MB, requests send at most 256 KB and last at most 10 seconds, and each copy of a plugin makes at most 60 requests per hour. `meshloom.app` servers are reserved to Meshloom's own plugins. The log shows each request without the part of the address after `?`, and so does the HTTP request line Meshloom logs for every outgoing request (webhook tokens included). Reaching a device on your local network, accepted by name, comes later. Nothing installs plugins yet.

- **The Control journal is now a preset of the Feed.** **Control** opens the Feed with only the *Control* source on and the Nodes facet (open on a phone), in place of the separate Control journal page, which is gone. `#control`, bookmarks, kiosk URLs and a stored `control` rail entry or last-viewed page keep working and land on that preset; the address bar still shows `#control`. Changes to the chips in the preset are not remembered, so the regular Feed keeps your filters. **Control** is no longer in the default icon bar or in the phone's Tools list (use **Feed**); it stays available in **Settings → Navigation** and in the search palette as *Control (feed preset)*.
- **A login page and a first-run page.** Opening Meshloom now shows the page that fits: on a new installation, **Create the administrator account** (user name, a password of at least 12 characters with a length hint, an optional display name, and the interface language); on an instance that already had data, the same page also asks for the **installation code** and says where to find it (Docker log, `journalctl -u meshloom`, the add-on's Log tab, or `meshloom-admin setup-code`); otherwise a sign-in page, which warns when the page is not served over HTTPS. Nothing of the app loads before that, and after the account is created or you sign in it opens without reloading. When a session ends while Meshloom is open, a dialog asks you to sign in again and you carry on exactly where you were; the live connection reconnects by itself. Your name sits in a new account button (bottom of the side rail, top of **Settings** on a phone) with **Sign out**. Instances behind Basic auth that already authenticate see no login page.
- **Home Assistant signs you in to the add-on.** Opened from the Home Assistant sidebar, Meshloom now uses your Home Assistant user: no Meshloom password, no account creation page and no installation code, also right after an update from 4.18. The first Home Assistant user to open it becomes the administrator; the next ones get the Member role. Meshloom trusts that identity only when it comes from Home Assistant's Supervisor, sets no cookie of its own there (every add-on shares Home Assistant's address), and a published port 8000 still asks for a Meshloom account and password. Opened through the sidebar without a Home Assistant user, it explains how to get in instead of showing a password form. The add-on's `basic_auth_username` and `basic_auth_password` options are deprecated and will be removed in the next release: set, they become a Meshloom account once, at the first start while there is no account. Listing these accounts and changing their role in Settings comes with the Users & Access section.
- **Forwarded headers are believed only from the trusted proxies you list.** The Docker image, the Linux package, the add-on, the service installer and the documented commands now start the server with `--no-proxy-headers`, and Meshloom reads `X-Forwarded-For` and `X-Forwarded-Proto` itself, only from the addresses in the new **Settings → Local Configuration → Trusted proxies** (empty by default, `127.0.0.1` included only if you add it). Before, a program on the same machine could claim any address, including Home Assistant's Supervisor. From a listed proxy, sign-in delays, the audit log and sessions get the visitor's address (the rightmost one the proxy chain added, never one the visitor wrote), and `X-Forwarded-Proto: https` gives the secure session cookie. **Behind a reverse proxy, list its own address** (never a Docker network or its gateway, which would also trust clients reaching a published port through it; the settings page warns about such an entry): otherwise every visitor has the proxy's address and, over HTTPS, the session cookie is the plain one, which the sign-in page and the settings now point out. Examples for Caddy, nginx and Traefik are in the HTTPS page of the documentation. If you start Meshloom with your own command, add `--no-proxy-headers` to it.
- **A Share section in Settings, off by default.** **Settings → Share: links and files** shows whether sharing is on, whether this Meshloom's sharing identity is ready and whether encrypted files for direct messages are available. Turning sharing on always opens a consent dialog listing exactly what leaves this Meshloom when you send (the file or URL, the sharing public key, the IP address) and when you read (the id of each object you display, the IP address), who runs the service, how long things are kept, and a required checkbox to accept the terms. Cancelling leaves it off. The section also sets when links are shortened (automatic, always, never) and when previews are fetched.
- **Short links show where they lead.** When sharing is on, a short link in a message (`meshl.ink/…`) shows a small `→ example.org` hint once you hover or focus it, asked once per link and remembered for ten minutes. Nothing is requested when the message is shown, and nothing at all when sharing is off. A conversation whose last message is a short link now reads `🔗` followed by its caption (or “Link”) in the list.
- **Shared images show in the conversation.** When sharing is on, a message sent as `📷 meshl.ink/…` shows the image under its text (at most 240×240, a click opens it full size; over 8 MiB a **Download the image** card instead). **Settings → Share: links and files → Previews of shared images and links** decides whether it loads by itself: *Always*, *Private conversations only* (direct messages and channels whose key is not derived from their name) or *Never*; otherwise a **Tap to load the image** button. Until you tap, nothing leaves your browser or this Meshloom, not even a question about the image, and hovering the link asks nothing either. An image from a blocked sender, or shown before the settings are loaded, never loads by itself; the Feed display screen shows a plain *Image not loaded* label instead of the button. An expired or removed image says so. Members and administrators can **Report** an image to the service from its card (8 reasons, optional details, 5 reports a day for this Meshloom; `POST /api/share/{id}/report`, signed with this Meshloom's key). The conversation list reads `📷 Image` or `🔒 Encrypted file`, never the address. `GET /api/channels` returns `key_derived`, and `share_conversation_id`, the opaque id an encrypted upload names a channel by (`X-Share-Conversation-Id`), so the channel key never travels back from the browser. English and French.
- **A Shorten button in the message composer.** When sharing is on and a message is over the size limit because of a long web address, a **Shorten** button appears next to the byte counter: it replaces each long address in the text box with a short `meshl.ink` link, shows the new size and offers **Undo**. Nothing is sent by this step, and Send still refuses a message that stays too long. The sharing setting decides the behaviour: *automatic* (the button), *always* (Send shortens the addresses first, then checks the size and sends) or *never*. Short links, `g:`/`m:`/`r:` messages and text without a long address are left alone; if the service refuses, your text is kept as it was. While sharing is off nothing is requested.
- **My uploads, and a Clear cache button, in the Share section.** **Settings → Share: links and files → My uploads** lists the links and files shared from this Meshloom (kind, file type or link's site, size, expiry, status), with **Refresh** and **Show more**. **Delete** asks for a confirmation, then removes the object from the share service and from this Meshloom's cache, so it stops loading at once. An object the service blocked is greyed, says why (spam, malware...) and links the service's contact page; it cannot be deleted. Members see this list (and only this list) in the section; administrators also get **Clear the cache**, which says how much space it freed. While sharing is off there is no list, and the section says why: Meshloom does not contact the service. `GET /api/share/mine` and `DELETE /api/share/{id}` (members and administrators; a viewer or kiosk is refused). English and French.
- **A Short links page in the documentation.** The new **Short links** page (Go deeper) explains the optional sharing service end to end: it is off by default and Meshloom makes no connection to it, not even a DNS lookup, until an administrator accepts the consent window; what leaves when you shorten a link and when you hover over one; the sharing key, which is never the radio's and which backups and restores never carry (a restore never turns sharing on and always creates a new key); the three shortening modes and the Shorten button; the addresses Meshloom never sends (credentials, IP addresses, local names); the `+` preview page and the service's report page; and what a ban means for **this instance**. **Messages**, **Security** (“What leaves your machine” and “The sharing key”) and **A trusted network** now mention it. Reporting a short link from Meshloom is not in this version (the service's preview page has a **Report** button).
- **Administrators manage the accounts through the API.** `GET`/`POST /api/auth/users`, `PATCH`/`DELETE /api/auth/users/{id}`, `POST /api/auth/users/{id}/password` and `DELETE /api/auth/users/{id}/sessions` (administrators only) list the accounts, Home Assistant ones included with their origin, create one with any of the four roles (an initial password for every role but `kiosk`, to change at the first sign-in), change its role or name, disable, enable or delete it, set its password and sign it out everywhere. The last enabled administrator can never be demoted, disabled or deleted (`auth_last_admin`). A role change, a disable, a deletion or a password set by an administrator signs the account out at once, live connections included, and forgets its remembered browsers. A Home Assistant account takes any role, `kiosk` included, but never a password; the role given to new Home Assistant users is `ingress_default_role` in `GET`/`PATCH /api/auth/settings`, and only the very first Home Assistant user ever becomes administrator, so deleting it does not make it administrator again. Every change is written to the audit log, refusals included. The Settings screen for it comes later.
- **A password set by an administrator must be changed first.** An account flagged this way (by an administrator, or `meshloom-admin reset-password --must-change`) can only read its profile, change its password with `PATCH /api/auth/me`, or sign out until it does; everything else answers `auth_password_change_required`, and `GET /api/auth/session` says `must_change_password`. Changing your own password signs your other browsers out, live connections included; a wrong current password goes through the same sign-in delays as the login page.

- **The interface only offers what your role can do.** A viewer sees no composer, no new-conversation button and no pin, favourite, mute or delete, and only **Local configuration** in Settings. A member can send and manage channels and contacts but never sees fanout, backup, the radio, updates or the other administrator sections, nor the repeater and room panes, the mesh test or blocking. A kiosk display shows channels only: no direct messages (no list entry, no Direct filter, none in the search palette), no composer, no Settings door; its account button only changes the language and the theme. Nothing is protected by this: the server still refuses every request outside the role, and an action it refuses shows one translated message (“This account is not allowed to do that.”) instead of the server's text. A refused read, such as a direct-message state on a kiosk, never raises an error.
- **Other roles no longer ask for administrator-only data at startup.** Members, viewers and kiosks do not request the radio configuration, the app settings, the update status, Community or push preferences, which the server only gives administrators (the page used to collect refusals). Known limit: these roles use the default theme and navigation, and blocked senders and names are not hidden for them.
- **A forced password change page.** When an administrator sets your password, Meshloom shows a page asking for the current and a new password (with the password rules) instead of the app, and carries on once it is changed. Any request answered "password change required" brings you to that page too.
- **Remote access on your own (documentation).** A new page, [Remote access on your own](docs/user/en/deep/remote-self-hosted.md), explains how to reach Meshloom from outside with your own Cloudflare Tunnel (and Access in front), Tailscale or WireGuard, or a Caddy, nginx or Traefik reverse proxy, and what to declare in Meshloom: public addresses, trusted proxies, the Host guard mode, trusted networks left off behind a proxy, HSTS, passkeys on https only, Web Push and the Home Assistant add-on. It is linked from the HTTPS, Accounts and access, Notifications and Install pages.
- **Notification language setting.** **Settings → Notifications → Notification language** (Automatic, Français, English; `notification_language` in `GET`/`PATCH /api/settings`) sets the language of e-mail and Apprise notifications. Automatic keeps the previous behaviour (English only when most subscribed browsers use English, French on a tie); picking a language applies it even when push subscriptions use another one. Push notifications keep each browser's language. The wording of every notification now comes from one renderer, with no change to any existing message.
- **Plugin instances API.** `/api/plugins` manages plugin instances: `GET /api/plugins` and `GET /api/plugins/bundled` (members and administrators; the config, code and errors are administrator-only fields), `POST /api/plugins/install` (a bundled plugin, refused with `consent_required` unless the granted permissions cover what the plugin asks for; a missing required config field creates the instance stopped with `attention.type = config_required`), `POST /api/plugins` (a local plugin from a template), `PATCH /api/plugins/{id}` (enable or disable starts or stops its process; label, config checked against the plugin's schema, scope, permissions, quotas never above the caps, auto-update, order), `PUT /api/plugins/{id}/source` (a local plugin's code, syntax checked, restarted), `POST .../restart`, `POST .../check`, `POST .../test` (a synthetic message: the replies are captured and never sent to the radio), `GET .../logs`, `GET .../diagnostic` and `DELETE /api/plugins/{id}?keep_data=`. Secret config fields are write-only and scrubbed from logs, diagnostics and test output, plugin logs are not part of `/api/debug`, and the `plugin_status` WebSocket event (administrators only) now carries the `attention` reason. Bundled plugins themselves arrive with a later change; nothing is installable yet.
- **Native e-mail, webhook and Apprise transports for notification channels.** A channel of type e-mail takes several recipients (one per line) and an optional Cc (each address gets its own message), and renders its message in the channel's own language; when the channel withholds a message's text, the subject is a neutral "New message". A webhook channel now sends `X-Webhook-Id` (the same on every retry, so a receiver can drop a duplicate) and `X-Webhook-Timestamp` next to the unchanged signature, and can turn certificate checks off, only for a destination on a private network, and an e-mail login is never sent without encryption outside it. An Apprise channel sends each URL on its own: one failing URL is reported by its line and never makes the others receive the alert twice. The existing destinations send the new webhook headers too.

### Changed

- **Breaking: Python bots are retired and converted into local extensions.** The `bot` fanout type no longer exists. At the first start of 5.0.0 (migration 122), every bot becomes a local extension named after it, with its code, its conversations and its on/off setting kept. A converted extension never starts by itself: it needs attention (*Review permissions*) and stays off until an administrator accepts what it may do (read every channel, direct messages and sent messages; reply in the conversation of the message it answers) in **Settings → Extensions**. The original bots are copied to the `fanout_configs_bots_archive` table (not in backups nor `/api/debug`, purged in 5.2.0). Bots no longer run inside the server process: no user code is `exec`'d there any more. See [Plugins](docs/user/en/deep/plugins.md#bots-from-418-and-earlier).
- **Breaking: the bot routes answer or disappear.** `POST /api/fanout` with `type: "bot"`, and `PATCH /api/fanout/{id}` on an archived bot, answer `410` with the code `bot_type_retired`. `POST /api/fanout/bots/disable-until-restart` is removed (404): use the global pause of **Settings → Extensions** or `MESHCORE_DISABLE_PLUGINS`. The `bot.created`, `bot.changed`, `bot.deleted` and `bots.disabled` audit actions are no longer written (old entries still show).
- **Breaking: `MESHCORE_DISABLE_BOTS` is retired.** It no longer locks anything. If it is still set, Meshloom logs a warning at start that names `MESHCORE_DISABLE_PLUGINS`, the lock of every extension (see [Environment](docs/user/en/deep/environment.md)); migration 122 reads it one last time and, when it is true, stores every converted extension switched off. The Linux package and the Raspberry Pi image no longer set any lock in `meshloom.env` (a commented `MESHCORE_DISABLE_PLUGINS` is there instead); `.env.example`, the Compose files and the documentation follow.
- **Breaking: the Home Assistant add-on option `disable_bots` is removed.** Use `disable_plugins`. If a leftover `disable_bots: true` reaches the add-on, it is passed to migration 122 once (the converted extensions arrive switched off, as with the package) and the add-on log says that the option is gone.
- **Deprecated: `bots_disabled` and `bots_disabled_source` in `/api/health`.** They stay for one release and now follow the extension lock and pause (`env` or `paused`; `until_restart` never appears). `/api/health` gains `converted_bots` (`{converted, attention}`, `null` once an administrator dismisses the notice with `POST /api/plugins/converted-bots/acknowledge`). `/api/debug` no longer lists `disable_bots`.
- **Backups convert bots.** New backups hold no bot entries. A bot entry in a backup made before 5.0.0 is restored as a local extension, switched off and waiting for review; the summary names them (`bots_converted`), and the old "bots skipped while bots are disabled" case is gone.
- **The unprotected-bot security warning is removed.** The full-screen "Unprotected bot execution is enabled" dialog and its **Disable Bots Until Server Restart** button no longer exist, with the bots.
- **Downgrading after this update.** Going back to a version older than 5.0 reads the old notification settings again: **channels added after the update (ntfy, Discord, Telegram, Gotify, Home Assistant, a second e-mail or webhook) are not visible after a downgrade**, and the rules you changed in the table are not read. Note down the destinations you need first, or restore a backup taken before the update.
- **Deprecated notification endpoints.** `GET`/`PATCH /api/push/preferences`, `PUT /api/push/preferences/conversations/{key}`, `notification_destinations` in `PATCH /api/settings` and `POST /api/settings/notification-destinations/test` answer a `Deprecation` header and will be removed in 5.2.0; use `/api/notifications`. They keep working and are mirrored into the rules for push, e-mail, webhook and Apprise. A `PATCH` of `config`, `allow_private_network` or `verify_tls`, or a `DELETE`, on a `legacy:` channel still answers `409 legacy_managed`: those three destinations are edited with their own form.
- **Mentions from the mesh are broken in plain-text channels too.** The shared escaper that Discord introduced applies to every ntfy, Discord, Apprise and preset (Telegram, Gotify, Home Assistant) notification, in plain text as well as Markdown (plain-text channels were described as unchanged): `@everyone`, `@here`, `@channel`, `@all`, `@room`, `<@`, `<!` and now `<#` get an invisible character, and `@everyone` or `@here` are broken even when a letter follows. Nothing changes in how a message reads, except for these sequences.
- **Blocked senders and muted channels never notify, on every channel.** A message from a contact or name on your block lists no longer produces a notification for any channel (Web Push, e-mail, webhook, Apprise, and the channels to come), where 4.18.x only held it back from Apprise; a muted channel (until the mute ends) stays silent on every channel, exceptions included. An event marked *always deliver* still goes through. Nothing changes until you block someone or mute a channel. The block lists are kept in memory, so a message costs no extra database read.
- **Notifications are delivered from the rules.** Every notification producer (new messages, a contact seen for the first time, a channel found, telemetry alerts, new releases, radio offline and online, start-up) now hands its event to the notification dispatcher, and the rules of `/api/notifications/rules` choose the channels: a conversation exception for a channel wins, a direct message follows `message.dm`, a Public or `#` channel follows `message.channel`, and a channel with its own key never notifies without an exception. Nothing changes if you never touch the new settings: the preferences you set in Settings → Notifications and the bell are kept in the rules and the results are the same (the notification texts are byte for byte the same). What the new API changes in the rules is copied back, so the deprecated `/api/push/preferences` still shows it. Telemetry alert rules no longer store a `channels` field (an old request that still sends one is accepted and ignored: the notification rules choose the channels).

- **Notifications go through a queue per destination.** Push, e-mail, webhook and Apprise each get their own queue and send one notification at a time, so a slow or failing service no longer delays the others or push. E-mail sends at most one notification every 10 seconds. The "+N events" grouping and the cap on new contacts, until now only on Apprise, apply to every destination, push, e-mail and webhook included: past 20 waiting for one destination, the rest become a single "+N events" (never for urgent ones), and new contacts notify at most 10 times an hour per destination, then one "+N new contacts". A webhook that is down, times out or asks to slow down is now tried again after 5 s and 30 s (never when it refuses the request); a message notification that still fails is dropped, a radio offline or telemetry alert is tried once more 5 minutes later when the service was unreachable, never after it refused the request. `/api/health` now gives each destination's state under `notification_statuses`, keyed by its id (`builtin:push`, `legacy:email`, `legacy:webhook`, `legacy:apprise`), with `status`, the last error code and the last success, and the live connection sends `health` when it changes; the former `notification_statuses.webhook` and `.apprise` entries are gone. Failed, grouped, dropped and cancelled notifications are logged without their text; successful sends are only counted. Notifications still waiting at shutdown are not sent after the restart.
- **Notification destinations move to channels (no visible change yet).** On upgrade, Meshloom turns the Web Push, e-mail, webhook and Apprise preferences into notification channels (`builtin:push`, `legacy:email`, `legacy:webhook`, `legacy:apprise`) and your per-event and per-conversation choices into notification rules, kept in new tables and `notification_rules`. Nothing is lost and delivery is unchanged for now: sending still follows the previous settings until the new dispatcher lands, and the old columns are kept. Channel secrets are write-only: no API returns them. The destination checks of the egress guard apply to every new channel.
- **The "Recover access" help page is now "Accounts and access", and the security pages describe 5.0.** One page covers roles, sessions, trusted networks, tokens, two-factor authentication and passkeys, Home Assistant, the kiosk screen, the 4.x Basic login window (until 5.2), the audit log and `meshloom-admin`; "A trusted network", "Security", the install pages, the README and `SECURITY.md` no longer say there are no accounts or that CORS is open, and say plainly that the radio proxy has no login.

- **`/api/health` and `/api/debug` no longer report `basic_auth_enabled`; the retired Basic variables only warn.** The field is gone from both payloads (the interface already reads `GET /api/auth/session`). `MESHCORE_BASIC_AUTH_USERNAME` and `_PASSWORD` are listed with the other retired variables: the first start with no account still turns them into the administrator, and every start logs a warning pointing to **Settings → Users & Access**. Setting only one no longer stops Meshloom from starting; it imports nothing. `Authorization: Basic` stays accepted until 5.2 and logs a deprecation warning the first time it is used after each start.

- **Bulk room decryption shows a translated completion message.** After decrypting old packets for several rooms at once, the confirmation now follows your language like the single-room one. Messages decrypted this way are also flagged as old on the live connection (`historical: true`, never sent to bots or webhooks), so a future client can tell them from new ones.
- **The meshcore.db download no longer holds the Bluetooth PIN.** The database copy from **Settings → Database** now leaves out the Bluetooth PIN, like the full backup, and the keys Meshloom will use to identify itself to online services. Putting such a copy back in place counts as a restore when Meshloom next starts.
- **Restore files are limited to 64 MiB.** A larger file is refused with a clear error.
- **Restoring an old backup no longer leaves a stale warning about local-network push.** When a restore turns local-network push back on for your subscriptions, the notice asking you to review it is cleared too.

### Fixed

- **Notification limits hold under a flood.** The pending and stored dedup keys are now bounded and trimmed, the global cap is checked and charged in one step, and the grouped summaries count against the 600-an-hour cap.
- **Apprise links point to the current documentation.** The "Apprise URL formats" links in Settings → Notifications and Settings → Fanout, and in the push documentation, went to the archived Apprise wiki. They now open the Apprise URL syntax page on appriseit.com.

### Security

- **Each extension copy runs under its own account, and Meshloom no longer needs the right to kill any process.** In the Docker image and the Home Assistant add-on, every copy of an extension now runs under a numeric account of its own (40000 to 49999, kept for as long as the copy exists) instead of the shared `meshloom-plugin` account, so one extension can no longer read another one's temporary files or stop it. Deleting a copy removes the temporary files its account left and frees the number, and the first start after the update cleans what the shared account left. With `MESHLOOM_RUN_AS_USER`, Meshloom now keeps only `CAP_SETUID` and `CAP_SETGID`: `CAP_KILL` is gone, a small helper running under the extension's own account stops it (migration 121). The documentation of the host lock (`MESHCORE_DISABLE_PLUGINS`, the add-on's `disable_plugins`) now says what it really does: it refuses installing, changing and starting extensions, and still allows viewing, deleting and the global pause. With host networking, a default route over a VLAN or macvlan interface is now recognised, so extensions can reach the LAN router there as with a plain interface. In English and French.
- **Plugins can no longer start a program or a thread, even when Meshloom runs as root.** The plugin process now installs a kernel filter (seccomp) that refuses every new process, thread and program, on top of its existing limits, which root ignored: a Meshloom started as root without the plugin account (so at the bubblewrap or hooks-only level) could let a hostile plugin run a program. A plugin process running as root that cannot install the filter now refuses to start. With `MESHLOOM_RUN_AS_USER`, the Docker image keeps only the two rights needed to run plugins under another account (`CAP_SETUID`, `CAP_SETGID`), so plugins run under their own account (`setuid` level) instead of hooks only; the plugin process drops them before running any plugin code. The Linux package recommends bubblewrap, and its service may run at most 512 processes and threads (`TasksMax`). The Home Assistant add-on has a `disable_plugins` option that keeps every plugin from starting.
- **Only administrators change extensions, and every change is audited.** Installing, configuring, enabling, disabling, restarting, testing, editing and uninstalling an extension, and reading its log or diagnostic, need an administrator; members see **Settings → Extensions** read-only with a note saying why, viewer and kiosk accounts have no such section. Each change is written to the audit log with who made it and which extension (`plugin.installed`, `plugin.created`, `plugin.enabled`, `plugin.disabled`, `plugin.config_changed`, `plugin.changed`, `plugin.updated`, `plugin.removed`), with the names of the changed fields only: never a setting value, a secret or the code. Being on the local network gives no right to install anything; the trust page now says so. English and French.
- **Push devices belong to their account.** Each account now sees, renames, tests and removes only the browsers it subscribed to push notifications; another account's device is invisible to it. An administrator also sees the other accounts' devices (label, account and push service, never the full address) and can remove one, but not rename or test it. A device is notified only while its account is enabled and may send messages, and deleting an account deletes its devices. Devices registered before this version, and devices restored from a backup, belong to the first administrator.
- **The live connection sends each event only to the roles allowed to receive it, and never an event it cannot classify.** Every kind of live event is now listed once on the server with the permission needed to receive it, and the browser's list is generated from it. An event of a kind missing from that list is sent to no one, administrators included (it used to reach administrators). A kiosk still gets channel messages, sent and historical ones included, and never a direct message, nor who sent you one or when (contact updates no longer carry that activity, as the API already did), nor delivery confirmations of direct messages. Error details of integrations, notification channels, the radio proxy and plugins, plugin status changes and notifications (connected brokers, radio errors) now reach administrators only; other roles still see whether each integration works. Notifications about a send or a historical decryption still reach the roles that can see it. Plugin status changes no longer show up as unknown events in the browser console.
- **Security headers.** Meshloom now sends `Strict-Transport-Security` when you reach it directly over HTTPS (never over plain HTTP, the Home Assistant sidebar or the relay), marks the answers that carry secrets (the radio private key, backups, channel keys) `Cache-Control: no-store`, as sign-in answers already were, and sends a `Permissions-Policy` that turns the camera and microphone off. See the HTTPS page.
- **Signing a session out now cuts its live connection at once.** Signing out, revoking a session from the list, changing your password (which signs your other sessions out) or going past 50 sessions on one account (the oldest is signed out) now closes the live connection of the sessions concerned within a second, instead of leaving it open until the page reloads. A session signed out with `meshloom-admin`, an expired session or a disabled account loses its live connection within 30 seconds. A display that only keeps the live connection open, such as a kiosk screen, now stays signed in: the connection counts as a visit. Expired and signed-out sessions are deleted from the database every hour.
- **An audit log records sensitive actions.** Meshloom now writes one entry for each sign-in (successful or failed), sign-out, session signed out, account created, renamed, disabled or re-enabled, password change, first administrator setup, access settings change, bot created, changed or deleted, bots stopped, backup restore, private key import, radio factory reset and update started, whether it was done from the web interface, Home Assistant, `meshloom-admin` or by Meshloom itself (auto-update). Each entry holds the time, the account, the address, the way in (local network, Home Assistant, command line) and how it ended; never a password, a code, a session or a key. Administrators read it with `GET /api/auth/audit` (newest first, by pages); a viewer in Settings comes later. Entries are kept 366 days, then deleted every hour; administrators change that with `PATCH /api/auth/settings` (`audit_retention_days`, 1 to 36500).
- **Each account role is enforced on every route.** The server now checks the role of the account behind each request: `admin` can do everything, `member` sends and edits contacts and channels, `viewer` reads, and `kiosk` (a wall screen) reads channels only and never receives a direct message, from any route or the live connection. Every route declares the permission it needs, and a route without one is refused to everyone but an administrator. The API docs (`/docs`, `/openapi.json`) now need you to be signed in. The first administrator and the account imported from 4.x Basic auth keep full access, so an installation with one administrator sees no change.
- **Wrong passwords slow sign-in down, without ever locking an account.** After 5 wrong passwords for one account from one address, the next attempt waits 1 second, then 2, 4 and so on up to 1 minute; the sign-in page says how long. After 5 wrong passwords on an account from anywhere, it takes at most one attempt per second. An address past 100 wrong passwords in 15 minutes is refused for a while; IPv6 addresses count by their /64 block. A browser you signed in with is remembered by a cookie of its own and always gets in with the right password, whatever a guesser sends; it loses that pass after 5 wrong passwords (forgotten after 15 minutes without a new one), and a change of the account's password, role or name, disabling it, restoring a database copy or `meshloom-admin rotate-device-key` forgets it until the next sign-in (a restart keeps it). From a browser Meshloom does not know, a Docker install with no trusted proxy only has the one-attempt-a-second rule, which a fast guesser on the same address can hold; behind a reverse proxy not listed in Trusted proxies, every visitor counts as the proxy's address. Refused attempts cost no password check, and each delay is written once to the audit log.
- **An installation code from `meshloom-admin setup-code` is no longer replaced behind your back.** When Meshloom could not prepare its first start and retried it as you created the administrator, it issued a new code, so the one the command printed was refused. It now keeps it.
- **Signing in is now mandatory.** Every API route and the live connection now need an account; only a minimal health check (`/api/health` answers `{"ok": true}`), the login and the first-run setup stay open. On a new installation you create the administrator account first. On an instance that already had data and no password, Meshloom also asks for an **installation code** printed in the server log at each start, so that nobody else on your network can claim it after an automatic update. If you used `MESHCORE_BASIC_AUTH_USERNAME` / `MESHCORE_BASIC_AUTH_PASSWORD`, they become the administrator account on the first start (same name, same password), then are no longer read: remove them. Scripts and Home Assistant sensors that send that login with Basic auth keep working until 5.2. Passwords need at least 12 characters, must differ from the user name and cannot be a common password. Sessions last 30 days from the last visit; over HTTPS the cookie is restricted to this exact address. Changes (POST, PUT, PATCH, DELETE) coming from another web site are refused, unless that site is in **Allowed origins**; scripts that do not say where they come from are unaffected. Probes that read details from `/api/health` without signing in now get only `{"ok": true}`. Too many sign-in checks at once get a quick "retry in a moment" answer, and repeated wrong Basic logins from one address are refused for a few seconds. The Home Assistant add-on is the exception: see below.
- **A site in Allowed origins can now open the live connection too.** Until now a dashboard listed in **Settings > Local Configuration > Allowed origins** could call the API but its live connection (`/api/ws`) was refused. The API and the live connection now check the same list, compared the same way (letter case, a default port such as `:443` and international domain names make no difference). Nothing else is let in: an empty list still means same-origin only.
- **Other programs on the server can no longer read Meshloom's memory.** On Linux, the server now marks itself as not dumpable at startup, so another program running as the same user can no longer read its environment (the Basic auth login) or its memory (the radio's private key). Side effect: no core dump of the server, and tools such as `py-spy` or `gdb` need `sudo` to attach to it.
- **Other websites can no longer open the live connection.** A web page from another site, open in a browser that can reach Meshloom, could open the live connection (`/api/ws`) and receive your messages, packets and radio state as they arrive, with Basic auth on or not. The server now refuses that connection when the browser says it comes from another site; scripts and apps that do not say where they come from are unaffected. If you put Meshloom behind a reverse proxy that replaces the `Host` header, have it send the original one in `X-Forwarded-Host`, otherwise nothing arrives live (the Home Assistant sidebar already does).
- **Other web sites can no longer use Meshloom through your browser.** The server used to answer every web page with your credentials, so a site open in another tab could read your messages, your radio's private key and the full backup. It now answers only its own address (same-origin). Meshloom itself, the Vite dev server and Home Assistant ingress are unaffected. If a dashboard or a script served from another address must call the API, add that address in **Settings > Local Configuration > Allowed origins** (empty by default; no `*`).
- **Failed notification tests no longer reveal your network.** The **Test** buttons for a push subscription and for the e-mail and webhook destinations used to show the raw network error (connection refused, unknown host, HTTP status). A web page could use that answer to map the machines on your network. They now show a translated message saying only what kind of failure it was. The server's log records the error type, and for the webhook its host and HTTP status, but no longer the webhook address with its token.
- **Notifications no longer reach your private network unless you allow it.** The notification webhook and Web Push now refuse a destination on your LAN, on this machine or on a cloud metadata address, checked when you save it and again before each send, and connect only to the address that was checked (a DNS answer changing in between cannot redirect them; redirects are not followed). Tick **Allow a destination on a private network** for a webhook to Home Assistant, Node-RED or a script on your LAN; a webhook you had already set up keeps working, and a one-time notice tells you so. **Accept push endpoints on a private network** (Settings → Notifications) is for a self-hosted UnifiedPush distributor; if a device you had registered is not a browser push service, the upgrade ticks it for you so that device keeps receiving, and a notice says so. A refused destination answers at once, so a page can no longer time a test to find which machines exist, and a push test no longer answers a different status for a timeout. Proxy variables (`HTTPS_PROXY`) no longer apply to these two outputs.
- **Names that reverse text direction can no longer reorder the text around them.** The sender name in a message, the names in the reactions list, the chat title and the tooltips and screen-reader labels that mention a name are now isolated from their surroundings. A message with no sender at all and a right-to-left override, an isolate, a zero-width space or a similar invisible character is flagged as a possibly corrupt packet, like one with a control character; legitimate text (emoji sequences, Persian or Hindi text, Arabic or Hebrew direction marks) is not. Names in the conversation list and the feed are not isolated yet.
- **Fanout outputs no longer reach your private network unless you allow it, and Apprise accepts only reviewed services.** The Webhook, Apprise, Amazon SQS (custom endpoint) and Map Upload (custom API) integrations now refuse a destination on your LAN, on this machine or on a cloud metadata address, checked when you save an enabled integration and again before each send; tick **Allow a destination on a private network (LAN, localhost)** in the integration for Home Assistant, Gotify, ntfy or a script on your LAN. Integrations you had already set up keep working: the upgrade ticks the box for them and a one-time notice lists them; changing the address of one unticks the box. Apprise now accepts only a reviewed list of services (ntfy, Telegram, Discord, Gotify, Home Assistant, Matrix, e-mail, MQTT, Slack, Pushover, JSON/XML/form webhooks and a few more, see the Fanout page) and refuses the others, such as desktop notifications, syslog and Amazon SNS/SES, whatever the setting; a Telegram address needs a chat id; Apprise sends only to the host you typed (no redirect, no Matrix server discovery, no e-mail key lookup), restoring an older backup brings an Apprise integration with a refused address back disabled instead of dropping it, the Map Upload never follows a redirect, and the fanout webhook connects only to the address that was checked. Errors name the Apprise line at fault and no longer show the URL.
- **A push device whose address can never be allowed is removed.** Before each push, Meshloom checks the device's address again and connects only to the address it checked, with the certificate checked against the push service's name. A device whose address is malformed, or now points to a cloud metadata or documentation address (refused whatever the settings), is skipped and removed, with one line in the log (never its full address, which holds a token); when only the DNS answer points there, it is removed only if a push an hour or more later still meets it. Debug logs no longer show push addresses either: the HTTP library's request lines, which held them, are no longer logged at the debug level. A device refused only because **Accept push endpoints on a private network** is unticked, or whose name cannot be found for now, stays registered, and the log says so once.
- **Push notifications stay open to your LAN only when a device is really there.** The upgrade no longer turns on the private-network opt-in for a public UnifiedPush distributor such as ntfy.sh: only devices whose address is a private IP, a dotless name or a `.local`/`.lan`/`.home.arpa`/`.internal`/`.localdomain` name get it (no DNS lookup), and the one wrongly set by the previous build is withdrawn while the notice is still open, with a notice and a one-click button to allow it again when a device sits behind a name that may point to your network (split DNS, Tailscale). A push refused by that check no longer counts as a failure, so the device is not removed after repeated refusals. Restoring a backup taken before the protection now turns the opt-in on again for LAN devices, like the webhook.
- **Link and file sharing will sign with a key of its own, never the radio's.** Meshloom can now create an instance key for the coming sharing service: a random key kept in its database, unrelated to your radio's identity in the Community directory, so what you share cannot be tied to your node and a new radio keeps the same identity. It is created the first time sharing needs it, never appears in the settings API, a backup or the logs, and a restore gives the instance a new one. Community tokens are unchanged.

- **Integration secrets are write-only.** The API, the health payload and the debug snapshot no longer return the Apprise URLs, the webhook signing secret, the MQTT passwords or the Amazon SQS secret key and session token of your integrations: a stored secret reads as `********`, and the Apprise list shows only each service and host. In the editor, leave a secret field empty to keep it, type a new one to replace it, or press **Clear**. An integration's error no longer quotes the password or tokens of its Apprise URL; its service and host stay readable. Changing where a kept secret goes (a webhook URL, an MQTT broker, port or TLS, an SQS endpoint, the e-mail server, port or security, the notification webhook URL) now requires typing the secret again, so a stored secret can never be sent to a new address; a restore that points one elsewhere drops the secret with a warning. The JSON backup download (`GET /api/settings/backup/json`) no longer holds the e-mail (SMTP) password or the notification webhook secret: restoring it keeps the ones already stored. The full backup still carries them. A restored integration that was allowed to reach your private network comes back disabled, with a notice, so you can check it before turning it back on.
- **Webhook URLs, webhook headers and the SQS access key ID are write-only too.** A webhook URL often carries its token (Discord, Slack, Home Assistant), and so do custom headers such as `Authorization`. The API and the editor no longer show them: the integration list and the notification webhook show only the scheme and host (`https://hooks.example.org/****`), a header keeps its name with its value shown as `********`, and the Amazon SQS access key ID reads `********` like the secret key. Leave a field as it is to keep the stored value, or type a new one. Changing a webhook URL requires typing its header values and signing secret again, and a restore that points a webhook elsewhere drops them with a warning. An integration's error no longer quotes the path or query of its webhook URL, nor a header value. The JSON backup download (`GET /api/settings/backup/json`) no longer holds the notification webhook URL; restoring it keeps the stored one, and the full backup still carries it. Existing integrations, notification settings and backups from 4.18.x keep working without typing anything again.
---
- **Meshloom notes the names it is reached under (Host guard, observation mode).** Meshloom now checks the name in the browser's address against the names it knows: an IP address, `localhost`, `.local`, `.home.arpa`, `.lan` and `.internal` names, this machine's name, a declared public address and the Meshloom Remote name (Home Assistant ingress and the relay are never checked). In 5.0.0 any other name, such as the DNS name of your reverse proxy, still works: it is only logged once and kept in a list that `GET /api/auth/settings` returns (`rejected_hosts`, `host_guard_mode`), so your upgrade does not break. From 5.1 an unknown name will be refused with an error naming it (`421 auth_host_rejected`). A trusted network never signs anyone in under an unknown name, in either mode.
- **Public addresses and the Host guard mode in Settings.** **Settings → Users & Access → Public addresses** (administrators) lists the addresses Meshloom is reached under (`https://name[:port][/prefix]`), the unknown names the Host guard saw recently with an **Allow** button that declares one, and a switch from observation to strict mode (an unknown name then gets a 421 error that names it). Meshloom refuses to enforce, or to remove an address, when that would reject the address you are using right now; there is no environment variable. Also: only plain names (letters, digits, dots, hyphens) are recorded and logged, the log line for a new unknown name is rate limited, and the 421 answer now carries the security headers. Strict mode still lists the refused names, **Allow** asks you to confirm the exact name (they are supplied by the requester and may come from an attacker), international names are converted to `xn--` form, and `meshloom-admin host-guard-observe` switches back to observation from the machine.

### Français

Le serveur applique désormais la limite de taille des messages de la radio, quel que soit le mode d'envoi.

#### Avant de mettre à jour

La version 5.0.0 modifie votre base de données d’une façon qu’une version plus ancienne ne sait pas défaire. **Avant de mettre à jour, téléchargez une copie complète de la base** (**Réglages → Base de données → Télécharger meshcore.db**) et gardez-la : sans elle, il n’y a pas de retour possible vers la 4.18. Une sauvegarde faite en 5.0.0 n’est pas non plus comprise en entier par la 4.18 (voir plus bas). Le téléchargement est épuré : ni mot de passe ni secret à deux facteurs de compte, ni secret d’extension, ni session ouverte, et les consentements de partage et du Store sont remis à zéro. Le restaurer en 5.0.0 conserve les comptes mais personne ne peut se connecter par mot de passe tant que vous n’avez pas lancé `meshloom-admin`, et les extensions redemandent leurs secrets. Pour une restauration complète, gardez aussi une sauvegarde complète (**Réglages → Base de données → Sauvegarde et restauration**), qui porte les comptes et les extensions (secrets toujours exclus).

#### Ruptures

- **Les bots Python sont convertis en extensions locales** et plus aucun code utilisateur ne tourne dans le processus du serveur (l’`exec` a disparu). Chaque extension convertie reste éteinte tant qu’un administrateur n’a pas examiné ses permissions. Le type de fanout `bot` répond `410 bot_type_retired`, l’avertissement « L’exécution non protégée de bots est activée » et son bouton **Désactiver les bots jusqu’au redémarrage du serveur** (avec sa route) n’existent plus. Le code de conversion et la copie des bots d’origine (`fanout_configs_bots_archive`) sont retirés en 5.2.0. Détails sous *Modifications* et dans [Extensions](docs/user/fr/deep/plugins.md#les-bots-de-la-418-et-avant).
- **`MESHCORE_DISABLE_BOTS` et l’option d’add-on `disable_bots` sont supprimées, sans alias.** Une valeur résiduelle ne verrouille rien : elle est journalisée par un avertissement au démarrage qui nomme `MESHCORE_DISABLE_PLUGINS` (add-on : `disable_plugins`), le verrou de toutes les extensions. Seul usage restant : la migration unique des bots, qui enregistre les extensions converties éteintes si la variable (ou, dans l’add-on, `disable_bots: true`) est encore là.
- **Pas de retour à la 4.18.** La migration des bots s’exécute au premier démarrage et une version plus ancienne n’en lit pas le résultat : les bots convertis n’apparaissent pas comme des bots, et les canaux de notification ajoutés en 5.0.0 ne sont pas vus. Restaurez la copie de la base prise avant la mise à jour pour revenir en arrière.
- **La sauvegarde complète est au format v2** (`meshloom-backup-v2` : identité, radio, Wi-Fi, comptes, notifications, extensions). La 4.18 la lit comme un fichier v1 et ignore sans rien dire tout ce qu’elle ne connaît pas : restaurez un fichier v2 en 5.0.0 ou plus.
- **Le serveur applique la limite de taille des messages de la radio** pour tous les modes d’envoi (bots, scripts, API). Un message trop long est désormais refusé par le serveur ; jusqu’ici seul le composeur web le vérifiait.
- **`bots_disabled` et `bots_disabled_source` dans `/api/health` sont dépréciés**, conservés une version.
- **Les extensions demandent Linux ou macOS.** Sous Windows le système d’extensions est désactivé (`plugins_disabled_source: "platform"`). Sans bubblewrap et sans compte dédié (macOS, Linux sans eux), les extensions tournent au niveau plus faible *hooks seulement*, et le journal du serveur le dit à chaque démarrage.

#### Ajouts

- **Mettre en pause toutes les extensions.** **Réglages → Extensions** a un bouton **Mettre en pause toutes les extensions** (administrateurs, après confirmation), l’interrupteur des incidents : toutes les extensions en cours s’arrêtent, aucune ne démarre pendant la pause, les événements ne leur sont pas transmis et *Tester avec un message* répond `409 plugins_paused`, mais chaque extension garde son interrupteur, ses réglages et ses données. Un bandeau indique depuis quand et par qui, les extensions affichent *En pause*, et **Reprendre** relance exactement celles qui sont activées. La pause survit à un redémarrage et retient aussi les bots Python. `GET /api/plugins/pause` (membres et plus) et `PUT /api/plugins/pause` (administrateurs, idempotent, audité `plugins.paused` et `plugins.resumed`) ; `/api/health` indique `plugins_disabled`, `plugins_disabled_source: "paused"` et `plugins_paused_since`, `MESHCORE_DISABLE_PLUGINS` et la plateforme restant prioritaires (migration 120). Le guide compare la pause à la désactivation d’une extension et au verrou de l’hôte. En français et en anglais.
- **« Rester connecté » sur la page de connexion.** Le formulaire de connexion a une case **Rester connecté sur cet appareil**, décochée par défaut. Cochée, la session dure 30 jours, glissante et survit à la fermeture du navigateur. Décochée, le cookie n'a pas de `Max-Age` : le navigateur l'abandonne en quittant, et la session s'arrête aussi côté serveur après 12 heures sans activité (jamais plus que les 30 jours). `POST /api/auth/login` et `POST /api/auth/webauthn/login/verify` acceptent `remember` (`false` par défaut), conservé à la deuxième étape d'une connexion à deux facteurs (la migration 117 ajoute `sessions.remember` ; les sessions existantes gardent la durée longue). Sur la page de connexion, un navigateur qui gère les passkeys, à une adresse où le serveur ne les propose pas, explique maintenant pourquoi le bouton passkey est absent. Anglais et français.
- **Lignes d'événements dans le Fil.** La puce *Événements* du Fil, jusqu'ici indisponible, est activée par défaut et ajoute une ligne quand la radio passe hors ligne (après le délai *radio hors ligne* des règles de Notifications, lu par les administrateurs ; les autres sessions gardent la minute par défaut ; une coupure plus brève et les redémarrages lancés par Meshloom lui-même n'ajoutent rien) puis revient en ligne avec la durée d'absence, quand un nouveau contact est entendu (hors inconnus et salons, comme les notifications), quand un contact change de nom et quand un canal est découvert. Elles sont construites à partir des trames en direct que le navigateur reçoit déjà (aucune nouvelle route, rien n'est stocké), reprennent les identifiants d'événements de Notifications, se rangent avec les messages sur l'horloge du serveur, respectent les droits de la session (le lien vers *Canaux découverts* demande le droit d'envoyer, celui vers la radio le rôle administrateur, aucun en mode affichage) et ne sont conservées que pour la session, ce que le Fil indique. Un navigateur qui avait mémorisé ses filtres avant ce changement garde *Événements* désactivé jusqu'à ce qu'on coche la puce. Anglais et français.
- **Limites de notifications, regroupement et plafond global.** Chaque canal de notification a désormais de vraies limites par minute, par heure et par jour (Telegram 15 par minute et 200 par heure, Discord 30 et 600, e-mail 6, 60 et 200 par jour, webhook 120 par minute, Web Push 60, ntfy.sh 10, 30 et 200 par jour ; modifiables par canal), et la limite journalière par défaut de ntfy.sh est enfin **appliquée** au lieu d’être seulement affichée. Une limite par minute ou par heure atteinte n’abandonne plus rien : les notifications attendent une minute et partent en **un seul résumé** (« +14 messages : 12 dans #paris, 2 MP ») qui ne fait que compter, sans jamais porter le texte d’un message, un expéditeur ni le nom d’un canal privé. Les évènements urgents (radio hors ligne, alerte de télémétrie) prennent un jeton comme les autres mais passent une limite pleine une fois par évènement : un capteur qui oscille ne la contourne pas. Une limite journalière atteinte arrête le canal jusqu’au jour UTC suivant (le compte survit à un redémarrage) et s’inscrit *quota dépassé* ; tous canaux confondus, Meshloom envoie au plus 600 notifications par heure (`limits.global_per_hour` dans les règles), inscrit le reste *limitée* et indique `notifications_capped: true` dans `/api/health`. La déduplication survit aussi à un redémarrage : un nouveau contact notifie une fois par jour, une panne de radio une fois, un message une fois. Guide utilisateur mis à jour en français et en anglais.
- **Un guide des notifications, en français et en anglais.** La nouvelle page [Notifications](docs/user/fr/deep/notifications.md) (Aller plus loin) explique les canaux et leurs types, ce qui quitte votre machine et la politique de texte de chaque canal, les destinations sur votre réseau, les événements et le tableau des règles, les mentions, la cloche des conversations et les exceptions, Ne pas déranger, la sourdine et les listes de blocage, les sauvegardes sans secrets, le bouton de test et ses codes d’erreur, et le dépannage. La page Push ne garde que ce qui est propre aux navigateurs (HTTPS, l’adresse de contact VAPID, les appareils) ; les pages Fanout, Sécurité, Variables et Home Assistant renvoient vers la nouvelle, et la page Fanout dit désormais lequel de ses deux webhooks et quel Apprise est lequel.
- **La cloche des conversations liste vos canaux de notification.** Dans l'en-tête d'une conversation (réservée aux administrateurs : le kiosque, le lecteur et le membre n'ont pas de cloche), la cloche montre maintenant une case par canal activé, avec son icône, son nom et un point d'état, à la place des cases fixes Push, E-mail, Webhook et Apprise. Une case indique si le canal notifie cette conversation et pourquoi (*par défaut*, *activé ici*, *coupé ici*) ; la modifier crée une exception pour cette conversation, et revenir à ce que dit le défaut la supprime. Activer **Web Push** abonne d'abord le navigateur. Le menu contient aussi le raccourci **Ne pas déranger** (1 heure, 8 heures, jusqu'à demain 8:00, reprendre) et un lien vers les réglages des canaux ; le même menu se trouve sous les trois points d'une conversation dans la liste. Sur la page **Alertes**, la destination des alertes de télémétrie est maintenant un bouton par canal, la même ligne que dans le tableau des Notifications. Le miroir de la politique de conversation côté navigateur (`notificationPolicy.ts`) est testé sur les mêmes cas que le serveur. Anglais et français.
- **Canaux et règles de notification dans la sauvegarde.** La sauvegarde complète contient désormais une partie *Notifications* : chaque canal (type, nom, options, limites) et les règles, **sans aucun secret** : jetons, mots de passe et adresses qui portent un jeton ne quittent jamais Meshloom, et les destinations e-mail, webhook et Apprise des réglages ne sont plus écrites avec leurs secrets dans la sauvegarde complète non plus (une restauration garde ceux déjà enregistrés). Après une restauration, un canal qui a perdu un secret, obligatoire ou facultatif, revient **désactivé** et marqué *Secret manquant après une restauration* (français et anglais) ; il ne peut pas être activé tant que chaque secret n’est pas saisi à nouveau ou effacé volontairement. Les règles restaurées sont vérifiées comme le fait l’API des règles et les entrées mal formées sont laissées de côté ; un canal qui enfreint les règles d’un nouveau canal (second Web Push, identifiant invalide, champ obligatoire absent) est ignoré avec un avertissement. Meshloom n’envoie jamais avec un secret vide. Un canal déjà présent avec la même adresse garde son secret, et une pause n’est pas restaurée. Une sauvegarde faite par 4.18.x se restaure dans les mêmes canaux et règles que lors de la mise à jour. L’instantané de support (`GET /api/debug`) liste les canaux avec les noms de leurs secrets renseignés, jamais les valeurs.
- **Contrôles plus stricts des préréglages Telegram, Gotify et Home Assistant.** Les champs des préréglages n’acceptent que les chiffres et lettres ASCII (un jeton écrit avec des chiffres arabes-indiens passait et n’échouait qu’à l’envoi) ; un chemin Gotify contenant `..` ou `%` est refusé ; et une adresse Home Assistant avec un chemin (`https://hote/ha`, derrière un proxy inverse sur un sous-chemin) est désormais refusée, le champ étant signalé et son aide l’expliquant, au lieu de perdre le chemin sans rien dire. Français et anglais.
- **Canaux Telegram, Gotify et Home Assistant.** **Réglages → Notifications → Ajouter un canal** propose désormais trois **préréglages** : des formulaires à champs nommés pour des services que Meshloom joint par Apprise. **Telegram** (jeton du bot, en écriture seule ; identifiant de conversation, obligatoire ; sujet facultatif ; un tiers, donc le texte privé reste chez vous par défaut ; 15 messages par minute et 200 par heure, et le délai « trop de requêtes » de Telegram est respecté), **Gotify** (serveur et jeton d’application, en écriture seule ; priorité ; 60 par minute) et **Home Assistant** (adresse, jeton d’accès longue durée en écriture seule, et service notify de l’application Companion de votre téléphone ; 60 par minute). Chaque formulaire explique où trouver ses valeurs et renvoie à la documentation du service ; l’adresse Apprise est construite à partir des champs à l’envoi, vérifiée comme toute autre destination (un serveur sur votre réseau local demande **Autoriser une destination sur réseau privé**) et jamais conservée. Un préréglage est une donnée : un nouveau service n’exige aucun code. `GET /api/notifications/types` liste les préréglages avec `preset: true`, `base_type: "apprise"` et un schéma JSON construit à partir de leurs champs. Français et anglais.
- **Notifications de mention.** Un message de canal qui contient `@[nom de votre radio]` (quelle que soit la casse), ou le nom de votre radio comme mot entier quand **Mots entiers uniquement** est coché, notifie désormais les canaux cochés sur la ligne **Mentions** de **Réglages → Notifications**, sous la forme « Mention dans #paris par Léa », à la place de la notification ordinaire du canal sur ces canaux (jamais les deux). Elle n’est jamais urgente, la sourdine du canal, les listes de blocage et Ne pas déranger s’appliquent, et sur un canal à clé privée elle n’atteint que les canaux qui ne sont pas un tiers (un webhook en est toujours un) ou que vous avez autorisés pour cette conversation (n’importe qui à portée peut forger une mention). Les installations neuves et existantes reçoivent la ligne Mentions remplie avec les canaux des messages privés (migration 115). Anglais et français.
- **Canaux ntfy.** **Réglages → Notifications → Ajouter un canal → ntfy** envoie les notifications vers un sujet [ntfy](https://ntfy.sh) (ntfy.sh ou votre propre serveur) avec le client intégré à Meshloom, sans passer par une URL Apprise : le message est publié en JSON, les accents des titres fonctionnent et le sujet, seul secret sur ntfy.sh, n'apparaît jamais dans une URL ni dans un journal. Champs : serveur, sujet (en écriture seule, obligatoire), jeton d'accès ou nom d'utilisateur et mot de passe (en écriture seule), priorité des événements ordinaires (3) et urgents comme *radio hors ligne* (5), étiquettes, adresse de ce Meshloom (toucher la notification ouvre la conversation), Markdown, et vérification du certificat (désactivable seulement pour un serveur de votre réseau privé, avec l'option réseau privé). Les erreurs sont explicites : identifiants refusés, quota atteint, limite de débit (le `Retry-After` de ntfy est respecté), certificat en échec. Un canal ntfy.sh est traité comme un tiers (le texte des messages privés et des canaux privés reste chez vous par défaut) et affiche une limite par défaut de 10 messages par minute, 30 par heure et 200 par jour (l'offre gratuite de ntfy.sh en autorise 250 par jour) ; un serveur auto-hébergé n'a pas de limite journalière. Ces limites sont appliquées par le limiteur de notifications (voir *Limites de notifications, regroupement et plafond global*), et un quota que ntfy.sh signale lui-même comme épuisé s'affiche *quota atteint*. Le sujet est public par obscurité : prenez-en un long et aléatoire (par exemple `openssl rand -base64 18 | tr '+/' '-_'`) ou votre propre serveur. Un nouveau test d'intégration publie vers un vrai conteneur ntfy en CI.
- **Canal Discord.** **Réglages → Notifications → Ajouter un canal → Discord** publie sur un webhook Discord (`discord.com` ou `discordapp.com` uniquement ; l’URL est en écriture seule, avec un nom d’utilisateur facultatif et sans changement d’avatar). Chaque requête porte `allowed_mentions: {"parse": []}` : `@everyone`, `@here`, les membres et les rôles ne sont jamais mentionnés, le texte est échappé en Markdown, les aperçus de liens sont désactivés et les en-têtes de limite de débit de Discord ainsi que le `Retry-After` d’un `429` sont respectés. L’échappement Markdown (et la neutralisation des mentions) vit désormais dans un module partagé par tous les canaux Markdown, Apprise au format Markdown compris. Anglais et français.
- **Règles de notification dans les Réglages.** **Réglages → Notifications** propose désormais **Ne pas déranger** (1 heure, 8 heures ou jusqu’à demain 8 h, avec le temps restant et **Reprendre**), un tableau événements × canaux (événements regroupés en Messages, Nœuds, Radio, Télémétrie et Meshloom ; une case par canal, une colonne **Toujours livrer** qui passe la pause, des sous-lignes par type de nœud pour les nouveaux nœuds, le délai « radio hors ligne » et la case « mots entiers » des mentions près de leurs lignes, **Canal modifié** verrouillé sur Web Push, canaux désactivés grisés) et des exceptions de conversation avec un bouton par canal (hérite, oui, non) et un bouton de suppression. Le tableau défile dans sa boîte sur téléphone, garde sa première colonne visible et chaque case a une étiquette. Il remplace les colonnes fixes Push, E-mail, Webhook et Apprise et utilise l’API des règles. Français et anglais.
- **Canaux de notification dans les Réglages.** **Réglages → Notifications → Canaux** remplace les formulaires fixes e-mail, webhook et Apprise par une carte par canal (type, voyant d’état, dernière erreur, interrupteur, **Tester**, **Modifier**, **Supprimer**) ; la carte Web Push regroupe l’abonnement de ce navigateur, les appareils et le sujet VAPID, et indique quand Web Push est indisponible en HTTP. **Ajouter un canal** choisit un type ou un préréglage, affiche le formulaire décrit par le serveur (champs obligatoires étoilés, erreurs du serveur sous le champ, secrets jamais affichés et conservés sauf remplacement ou effacement), envoie un test **avant** d’enregistrer, puis demande **Que recevoir ici ?** (Comme Push, Alertes seulement ou Tout). Un service cloud affiche une note de confidentialité et le choix d’inclure le texte des messages (canaux publics seulement par défaut). Vos destinations e-mail, webhook et Apprise existantes (`legacy:*`) restent modifiables et supprimables depuis leur carte avec les formulaires 4.18.x, tant que les nouvelles règles ne livrent pas (les routes de canaux répondent `409 legacy_managed` pour elles). Français et anglais.
- **Réglages des extensions et extensions locales.** Dans **Réglages → Extensions**, le tiroir d'une extension a maintenant un formulaire **Réglages** généré à partir de la description que l'extension donne de ses propres réglages : les champs obligatoires portent une étoile, les erreurs de validation du serveur apparaissent sous le champ, et un réglage secret (clé d'API, mot de passe) n'est jamais affiché, même après l'enregistrement : le formulaire indique qu'une valeur est définie et propose **Remplacer** (vide, il la garde) et **Effacer** ; seul ce que vous changez est envoyé. Une extension qui attend des réglages obligatoires a un bouton **Aller aux réglages**. **Nouvelle extension locale** crée une extension Python à partir d'un modèle ; son tiroir a l'éditeur de code (enregistré après un contrôle de syntaxe, avec les remarques de l'analyse listées par ligne et les serveurs cités dans le code proposés avec un bouton **Autoriser**), les événements et permissions modifiables, **Vérifier** (erreurs et avertissements par ligne, rien n'est exécuté) et **Tester avec un message** (un faux message à travers l'extension en cours d'exécution, avec ses réponses et son journal ; rien n'est envoyé à la radio). Le formulaire est un composant partagé que les canaux de notification réutiliseront. `PATCH /api/plugins/{id}` accepte `hooks` pour une extension locale, et `PUT source` et `POST check` renvoient les hôtes `https://` trouvés dans le code (`http_hosts`).
- **Extensions dans les réglages.** La nouvelle section **Réglages → Extensions** (administrateurs) liste les extensions installées avec leur état, la raison pour laquelle l'une demande votre attention et si elle est fournie ; activez-en ou désactivez-en, réordonnez-les, ajoutez une extension fournie après avoir lu ce qu'elle peut faire (permissions en clair, serveurs contactés, déclaration de confidentialité, et un avertissement quand elle peut lire les messages privés et joindre Internet), et ouvrez un tiroir pour réduire ses permissions, choisir les conversations qu'elle lit, baisser ses limites d'envoi, lire son journal, la redémarrer, copier un diagnostic ou la désinstaller (en gardant ses données ou non). Une extension révoquée ou incompatible ne peut pas être activée, et les réglages secrets ne sont jamais affichés. Le haut de la page indique le niveau d'isolation en vigueur. `GET /api/plugins/bundled` renvoie aussi la déclaration `privacy` de chaque paquet.
- **Mode affichage du Fil (écrans muraux et kiosques).** Le bouton **Mode affichage** de l'en-tête du Fil, ou `display=1` dans l'adresse (aussi comme **Page de démarrage** ou dans `MESHLOOM_KIOSK_URL`), transforme le Fil en écran sans interface : texte agrandi de 25 % par l'échelle de police existante, ni barre latérale, ni barre du bas, ni en-tête, ni pastilles, ni facette Nœuds, le dernier message toujours suivi, séparateurs de jours, horloge discrète, ni flou ni ombre, et plein écran quand le navigateur l'autorise (ignoré sans bruit sinon ; sous Home Assistant il faut `allowfullscreen` sur le cadre de Home Assistant). Les messages directs sont masqués (la première requête porte `type=CHAN`) et les images et GIF ne sont pas chargés, quoi que dise l'adresse (`types=chan,priv` y est ignoré) ; les lignes sont en lecture seule et sans focus, et **Quitter** est le seul contrôle. C'est un choix d'affichage, pas un contrôle d'accès : le navigateur reçoit toujours toutes les trames, et c'est le rôle `kiosk` qui refuse les messages directs. **Quitter** rétablit les filtres précédents ; les filtres mémorisés, l'échelle de police et la préférence des réactions ne sont jamais réécrits. `docs/user/{en,fr}/rpi.md` gagne une section Mode affichage.
- **API des notifications.** `/api/notifications` (administrateurs) gère les canaux de notification : `GET /types` donne le schéma JSON de chaque type de canal (les champs secrets sont `writeOnly`), `GET`/`POST /channels` et `GET`/`PATCH`/`DELETE /channels/{id}` listent, créent, modifient et suppriment les canaux (les secrets s’affichent `********` ; les omettre, envoyer `null` ou `********` les conserve, `""` en efface un ; le type ne peut pas changer ; `builtin:push` ne peut pas être supprimé), `GET`/`PATCH /rules` et `PUT /rules/conversations/{key}` lisent et modifient les canaux atteints par chaque évènement et chaque conversation, la pause globale (`paused_until` : tout s’arrête sauf les évènements urgents et *toujours livrer*), le délai de radio hors ligne et le fuseau horaire de la machine (en lecture seule), et `GET /deliveries` et `GET /events` donnent le journal et le catalogue des évènements. `POST /channels/test` teste une configuration avant de l’enregistrer (rien n’est stocké) et `POST /channels/{id}/test` teste un canal enregistré, au plus une fois toutes les 5 secondes par canal (`429 rate_limited`) ; les deux passent par les mêmes contrôles réseau qu’un enregistrement. Les erreurs sont `{code, params}` en français et en anglais, sans jamais un hôte, une adresse ou un texte d’exception. Chaque création, changement de destination, activation, désactivation ou suppression d’un canal est écrit dans le journal d’audit, envoyé aux administrateurs en évènement temps réel `notification_channel_changed` et notifié par Web Push (qui ne peut pas être décoché) et par tous les autres canaux actifs. Les changements des règles et de la pause globale sont aussi audités. Les destinations des canaux `legacy:*` se règlent encore dans les Réglages (les nouvelles routes répondent `409 legacy_managed` pour un changement de destination et pour la suppression de ces canaux). `GET`/`PATCH /api/push/preferences`, `PUT /api/push/preferences/conversations/{key}`, `notification_destinations` dans `PATCH /api/settings` et `POST /api/settings/notification-destinations/test` fonctionnent toujours mais sont **dépréciés** (ils répondent un en-tête `Deprecation`) et seront retirés en 5.2.0 ; activer pour une conversation un média dont la destination n’est pas configurée répond désormais `400 channel_missing`, et le test de destination répond les codes ci-dessus au lieu de `notification_test_failed`.
- **Le Fil sous le rôle `kiosk`.** Une session qui n'a pas le droit de lire les messages directs (le rôle `kiosk`, détecté par capacité et non par nom de rôle) obtient un Fil sans puce **Messages directs** : la première requête porte `type=CHAN`, `types=priv` dans l'adresse et un filtre mémorisé sur les messages directs sont ignorés (et non réécrits en stockage), une trame de message direct qui parvient au navigateur est écartée avant d'entrer dans le Fil, et une liste refusée (403) affiche l'état vide habituel avec une explication au lieu d'une erreur. Le contrôle et les canaux en sourdine ou en attente restent lisibles. `docs/user/{en,fr}/around.md` et `rpi.md` le disent.
- **Page de démarrage.** **Réglages → Navigation → Page de démarrage** (administrateurs) choisit où Meshloom s'ouvre quand l'adresse n'a pas de partie `#` : les conversations (par défaut, inchangé), la carte ou n'importe quel outil, dont le Fil et son préréglage Contrôle. Elle est enregistrée avec l'instance (`ui_preferences.start_view`, refusée en 400 si ce n'est pas un outil connu) : elle s'applique à toutes les sessions et à tous les navigateurs, écran de kiosque compris. Un lien vers une conversation ou un outil l'emporte toujours, tout comme **Rouvrir la dernière vue** sur un appareil qui l'a activé et qui a quelque chose à rouvrir. Enregistrer la barre de navigation n'efface plus les autres préférences d'interface.
- **Filtres du fil dans l'adresse.** `#feed?src=messages,control,events&types=chan,priv&dir=in,out&muted=1&pending=1&node=<clé publique>&display=1` (et `#control?display=1`) fixe les filtres du fil dans l'adresse : un favori ou l'URL d'un kiosque ouvre la même vue sans dépendre du stockage du navigateur. L'adresse l'emporte sur les filtres mémorisés dans le navigateur, qui l'emportent sur les valeurs par défaut ; elle est appliquée avant la première requête et suit vos changements de pastilles (sans ajouter d'entrée d'historique). La clé d'un canal privé est son accès et les navigateurs synchronisent les favoris : l'adresse ne porte jamais de clé de canal, seulement des noms de sources et de types et la clé publique d'un nœud, et tout ce qui est inconnu ou mal formé y est ignoré. Le réglage de la page de démarrage accepte la même forme (`feed?...`, `control?...`), validée côté serveur par une liste blanche (400 sinon).
- **Les images kiosque ont un compte `kiosk` en lecture seule et affichent le code d’installation.** Une image kiosque Raspberry Pi se repère désormais par `/var/lib/meshloom/kiosk` (le paquet l’écrit sur les images kiosque gravées avant la 5.0), crée au premier démarrage un compte `kiosk` sans mot de passe et affiche **Compte administrateur pas encore créé** avec le code d’installation sur son propre écran, jusqu’à la création de l’administrateur depuis un autre appareil. Le paquet livre aussi le lanceur kiosque au même chemin ; il attend désormais `/api/auth/session`. L’écran du mur se connecte désormais tout seul (entrée suivante).
- **L’écran kiosque se connecte tout seul avec un jeton d’appareil.** Sur une image kiosque, Meshloom écrit un jeton d’appareil en lecture seule du compte `kiosk` dans `/var/lib/meshloom-kiosk/kiosk.token`, lisible seulement par l’utilisateur du bureau du Pi, et le lanceur l’échange contre une session par un lien à usage unique qui ne fonctionne que depuis le Pi lui-même (`POST /api/auth/kiosk-bootstrap`, `GET /api/auth/kiosk-session`) : le jeton n’apparaît jamais dans une adresse ni dans la liste des programmes en cours, et aucune règle de réseau de confiance n’est nécessaire. Tant qu’aucun administrateur n’existe, l’écran affiche la page d’installation avec son code, puis ouvre Meshloom tout seul une fois l’administrateur créé depuis un autre appareil. Révoquer **Kiosk screen** dans **Jetons d’API et d’appareil** déconnecte l’écran et écrit aussitôt un nouveau jeton, avec lequel l’écran se reconnecte en quelques secondes ; désactiver le compte `kiosk` garde l’écran déconnecté. Le jeton ne sert qu’à cette connexion, sur le Pi lui-même, et les deux routes sont limitées en débit. Une image kiosque gravée avant la 5.0 se connecte après un redémarrage suivant la mise à jour.
- **Double authentification (TOTP).** Tout compte qui a un mot de passe peut ajouter un code à 6 chiffres donné par une application d’authentification : `POST /api/auth/2fa/enrol` (avec le mot de passe) renvoie une seule fois le secret et son lien `otpauth://`, `POST /api/auth/2fa/confirm` l’active avec le premier code et renvoie dix codes de secours à usage unique. La connexion se fait alors en deux étapes : `POST /api/auth/login` répond par une connexion en attente au lieu d’une session, et `POST /api/auth/login/2fa` l’échange avec un code (ou un code de secours) contre la session. Un code ne sert qu’une fois, les mauvais codes comptent comme de mauvais mots de passe, l’authentification Basic cesse de fonctionner pour un tel compte, et `POST /api/auth/2fa/disable` / `POST /api/auth/2fa/recovery` demandent le mot de passe et un code. Un administrateur peut l’exiger pour les administrateurs avec `require_2fa_for_admins` (`PATCH /api/auth/settings`, désactivé par défaut) ; `meshloom-admin disable-2fa <utilisateur>` retire depuis la machine un second facteur perdu. La sauvegarde complète ne contient le secret et les hachés des codes de secours qu’avec **Inclure les comptes**.
- **Double authentification dans l’interface.** **Réglages → Utilisateurs et accès → Authentification à deux facteurs** la met en place : confirmez votre mot de passe, scannez le QR code (dessiné dans votre navigateur, la clé ne part vers aucun autre service) ou saisissez la clé, entrez le premier code, puis copiez ou téléchargez vos dix codes de secours, affichés une seule fois avec un avertissement. La même section génère de nouveaux codes de secours et la désactive, avec votre mot de passe et un code. La connexion affiche une seconde étape pour le code (ou un code de secours) avec des erreurs claires, et vous ramène au mot de passe si la connexion en attente a expiré. Un administrateur peut y cocher **Exiger l’authentification à deux facteurs pour les administrateurs** ; un administrateur qui ne l’a pas configurée ne voit alors que la page de configuration. Sous Home Assistant, ou pour un compte sans mot de passe, la section explique pourquoi elle ne s’applique pas.
- **Passkeys (côté serveur).** Un compte avec mot de passe peut enregistrer des passkeys (`POST /api/auth/webauthn/register/options` avec le mot de passe, et un code quand la double authentification est activée, puis `.../register/verify`), les lister et les retirer avec le mot de passe (`GET`/`DELETE /api/auth/webauthn/credentials`), et se connecter sans mot de passe (`POST /api/auth/webauthn/login/options` et `.../login/verify`, l’appareil doit vérifier l’utilisateur ; cela remplace à la fois le mot de passe et le code de double authentification). Avec la double authentification activée, une passkey peut remplacer le code à `POST /api/auth/login/2fa`. Les passkeys ne fonctionnent qu’en https sur une adresse publique déclarée, par Meshloom Remote ou sur `localhost` (`GET /api/auth/session` l’indique dans `methods.passkey`) ; le simple http sur le réseau local et l’ingress de Home Assistant sont refusés. Un défi ne sert qu’une fois en 5 minutes, les échecs comptent comme de mauvais mots de passe, un compteur qui recule est refusé, et `meshloom-admin remove-passkeys <utilisateur>` retire depuis la machine les passkeys d’un utilisateur. La sauvegarde complète ne les contient qu’avec **Inclure les comptes**.
- **Passkeys dans l’interface.** **Réglages → Utilisateurs et accès → Passkeys** liste vos passkeys (nom, ajout, dernière utilisation), en ajoute une (un nom, votre mot de passe et, avec la double authentification, un code, puis la fenêtre de votre navigateur) et en supprime une avec votre mot de passe ; la section prévient qu’un code de secours est consommé dès la validation, même si vous annulez la fenêtre du navigateur. La page de connexion gagne **Se connecter avec une passkey**, affiché seulement quand l’adresse accepte les passkeys et que le navigateur peut les utiliser (masqué avec une explication en simple http), et la seconde étape propose **Utiliser une passkey** aux comptes qui en ont une. La bibliothèque WebAuthn du navigateur (`@simplewebauthn/browser`) ne se charge qu’au début d’une action sur les passkeys, et les fenêtres annulées, refusées ou d’un appareil déjà enregistré ont chacune leur message. Le serveur journalise désormais un avertissement quand il refuse des connexions par passkey parce que sa table de défis en cours est pleine.
- **Jetons d’API et d’appareil.** Un script, un capteur Home Assistant ou une application mobile peut désormais se connecter avec son propre jeton plutôt qu’un mot de passe : `POST /api/auth/tokens` (membres et administrateurs, pour leur propre compte) renvoie une seule fois un jeton `mlt_…`, envoyé en `Authorization: Bearer` à chaque requête et sur la connexion en direct (`/api/ws`). Chaque jeton a des portées (`read`, `read_private`, `send`, `repeater_admin`, `admin`) qui ne dépassent jamais le rôle de son compte, une expiration facultative et sa dernière utilisation ; `DELETE /api/auth/tokens/<id>` le révoque aussitôt et ferme sa connexion ouverte. Meshloom ne garde qu’une empreinte de chaque jeton, ignore un jeton placé dans l’adresse, refuse les jetons passés par l’ingress de Home Assistant et inscrit au journal d’audit les créations, les révocations et les utilisations d’un jeton révoqué ou expiré. La sauvegarde complète les emporte avec les comptes.
- **Codes d’appairage d’appareil.** Un administrateur peut créer un code d’appairage à usage unique (`POST /api/auth/pairings` : un libellé, les portées de l’appareil, une durée de vie de 10 minutes au plus) et le texte de son QR code (`meshloom://pair?d=…`, qui porte le code, les adresses publiques https de l’instance et, si c’est une adresse du réseau privé, l’adresse locale utilisée par l’administrateur). Une application mobile comme meshenger envoie le code à `POST /api/auth/pair` avec un nom d’appareil et reçoit son propre jeton d’appareil, avec exactement ces portées, rattaché au compte qui a créé le code. Un code ne sert qu’une fois : une seconde utilisation, un code expiré ou révoqué répondent `auth_pairing_expired`. Meshloom ne garde qu’une empreinte de chaque code, ralentit le client qui envoie de mauvais codes (jamais tout le monde), refuse l’appairage à travers l’ingress de Home Assistant et inscrit au journal d’audit les créations, les révocations et les utilisations. Contrat pour les développeurs d’applications : `docs/dev/device-pairing.md`.
- **Appairer un appareil depuis les Réglages.** **Réglages → Utilisateurs et accès → Appairer un appareil** (administrateurs) crée un code d’appairage à partir d’un nom d’appareil, des portées qu’il obtient et d’une durée de 1 à 10 minutes, puis affiche son QR code (dessiné dans votre navigateur, chargé seulement au besoin) avec un compte à rebours en direct. Le QR code disparaît à l’expiration du code, **Annuler le code** l’arrête aussitôt, et le panneau liste les codes en attente avec un bouton d’annulation. La fenêtre explique à quoi sert le QR code (enrôler meshenger ou un autre appareil) et avertit qu’il ne doit pas être partagé. En français et en anglais.
- **Les jetons dans les réglages.** **Réglages → Utilisateurs et accès** a une nouvelle section **Jetons d’API et d’appareil** pour les membres et les administrateurs : créer un jeton (nom, portées permises par votre rôle, expiration ou aucune), le copier depuis un écran affiché une seule fois qui prévient qu’il ne sera plus montré, voir pour chaque jeton ses portées, sa création, sa dernière utilisation et son expiration (un administrateur voit aussi à qui il est), et en révoquer un après confirmation. Connecté par Home Assistant, la section explique que les jetons ne fonctionnent pas à travers l’ingress de l’add-on.
- **Réseaux de confiance.** Dans **Réglages → Utilisateurs et accès → Réseaux de confiance**, un administrateur peut connecter les appareils d'un réseau choisi avec un compte choisi, sans identification (vide par défaut ; `trusted_networks` dans `GET`/`PATCH /api/auth/settings`). Une règle ne s'applique jamais à une requête passée par un reverse-proxy ou un tunnel (en-têtes de transfert, ou pair listé comme proxy de confiance), par Meshloom Remote ou par l'ingress de Home Assistant, ni depuis le loopback hors de l'add-on, et seulement quand le navigateur utilise une adresse IP ou un nom local. Les règles qui recoupent un proxy de confiance sont refusées. Chaque connexion automatique est notée une fois par appareil et par jour dans le journal d'audit. Un avertissement rouge s'affiche quand une règle connecte un administrateur, et le nouvel avertissement de sécurité de l'accès, en haut d'Utilisateurs et accès, signale une règle administrateur alors que les plugins tournent avec une isolation réduite. Il signale aussi une règle dont le réseau est public ou aussi large que permis (une plage `/8` en IPv4 ou `/16` en IPv6). Une règle pour un compte qui doit changer son mot de passe est refusée tant que le mot de passe n'a pas été changé.
- **Journal d'audit dans les Réglages.** **Utilisateurs et accès** se termine désormais par un **Journal d'audit** réservé aux administrateurs : qui a fait quoi, du plus récent au plus ancien, avec l'heure, le compte (ou l'adresse) à l'origine, l'action, sa cible, le mode d'accès et le résultat, en français et en anglais. Filtrez par famille d'action, action, résultat, compte et dates ; chargez les entrées plus anciennes page par page. Les filtres s'appliquent aux entrées déjà chargées.

- **Utilisateurs et accès dans les Réglages.** La nouvelle section **Utilisateurs et accès** permet à un administrateur de lister les comptes (rôle, local ou Home Assistant, désactivé), d'en créer (membre, lecteur, kiosque), de changer un rôle, de désactiver ou réactiver, de réinitialiser un mot de passe (avec changement imposé à la prochaine connexion), de déconnecter partout, de supprimer, et de choisir le rôle des nouveaux utilisateurs Home Assistant. Chaque utilisateur y change son nom affiché et son mot de passe, voit ses sessions (appareil, adresse, dernière activité) et en révoque une ou toutes les autres. Les erreurs (dernier administrateur, mot de passe refusé, trop de tentatives, serveur occupé) sont expliquées en français et en anglais.
- **Les membres, lecteurs et kiosques reçoivent le thème et les listes de blocage de l'instance.** Une nouvelle route en lecture seule, `GET /api/settings/view`, ouverte à tous les rôles, ne renvoie que ce dont l'interface a besoin pour démarrer : le thème et la barre latérale, les clés et noms bloqués, les régions et les repères de non-lus (ceux des conversations directes sont retirés pour un kiosque). Rien d'administratif ni de secret n'y figure, et `GET /api/settings` reste réservé aux administrateurs. Jusqu'ici ces rôles tournaient sur les valeurs par défaut et voyaient encore les messages de contacts qu'un administrateur avait bloqués.
- **Les messages trop longs sont refusés par le serveur.** Jusqu'ici, seul le champ de saisie web vérifiait la taille : les robots et les clients de l'API pouvaient envoyer des messages que la radio ne peut pas transporter. Le serveur applique maintenant la même limite aux messages directs (156 octets) et aux messages de canal (156 octets moins le nom de votre nœud et le séparateur `: `), comptée en octets, sur tous les chemins y compris les renvois. Un message trop long est refusé avec une erreur claire, sans rien envoyer ni enregistrer. Limite connue : après avoir renommé votre nœud avec un nom plus long, un ancien message qui tenait peut être refusé au renvoi.
- **La sauvegarde complète contient les comptes.** **Réglages → Base de données** a une nouvelle case **Inclure les comptes**, cochée par défaut, avec un avertissement : le fichier contient alors des hachés de mots de passe et des secrets de double authentification (décochez-la avant d'envoyer une sauvegarde à quelqu'un). Restaurer un tel fichier sur une nouvelle machine rend les mêmes comptes et mots de passe ; cela remplace les comptes actuels et ferme toutes les sessions. Une restauration ne laisse jamais Meshloom sans administrateur actif qui peut s'y connecter (un compte avec mot de passe, ou un utilisateur Home Assistant sur l'add-on) : un fichier qui n'en a pas, ou sans comptes, conserve les comptes actuels. Les sessions de connexion, le journal d'audit, les codes d'appairage et l'identité Meshloom Remote ne sont jamais dans le fichier et sont ignorés si un fichier les contient. Un réseau de confiance dont le compte est une autre personne après la restauration est retiré.
- **La sauvegarde complète peut contenir des parties ajoutées par d'autres fonctions.** Des fonctions comme les comptes ou les extensions peuvent désormais ajouter leur propre partie à la sauvegarde complète. Avant la restauration, le résumé liste ces parties et signale celles qui contiennent des secrets ; une sauvegarde qui en contient demande à être chiffrée. Une partie que votre Meshloom ne connaît pas, ou faite par une version plus récente, est laissée de côté et le reste est restauré ; une partie en échec est laissée telle quelle. Les sauvegardes de la 4.18 et des versions antérieures se restaurent toujours. Restaurez une sauvegarde complète sur la 5.0 ou plus récent uniquement : la 4.18 n'en restaure que les contacts, canaux, réglages et groupes, sans le dire.
- **Deux nouveaux filtres sur `GET /api/messages`.** `outgoing=true|false` liste uniquement les messages envoyés ou reçus, et `since_id=N` renvoie les messages enregistrés après l'id N, dans l'ordre des id, historiques compris, pour qu'un client de l'API rattrape son retard sans trou. `since_id` ne se combine pas avec `before`, `after` ni `offset`.

- **Notifications « radio hors ligne », « radio de nouveau en ligne » et « Meshloom a démarré ».** Meshloom vous prévient désormais quand la radio est injoignable depuis plus de 60 secondes (urgent, une notification par panne), quand elle revient avec la durée de la panne, et à chaque démarrage de Meshloom (version, radio connectée ou non). Le push est actif par défaut ; l’e-mail et le webhook se cochent dans **Réglages → Notifications**, où le délai se règle aussi (0 notifie aussitôt). Rien n’est envoyé pour un redémarrage lancé par Meshloom lui-même, pendant une pause de la connexion, avant que la radio se soit connectée une fois après un démarrage, ni pendant le même délai juste après un démarrage : une mise à jour ne déclenche pas de fausse alerte. Ces notifications ne peuvent pas vous atteindre quand Meshloom lui-même est arrêté ; « Meshloom a démarré » révèle un redémarrage inattendu.
- **Le Fil affiche les noms, des séparateurs de jour et des puces de filtre.** Chaque message montre le nom de son canal ou de son contact, l'expéditeur (survolez-le pour voir le début de sa clé et distinguer deux personnes de même nom), l'heure, la région et une marque *envoyé* pour vos propres messages, avec un séparateur au début de chaque jour. Des puces au-dessus de la chronologie choisissent ce qui s'affiche : Canaux, Messages directs, Reçus, Envoyés, et les canaux en sourdine ou en attente, masqués par défaut avec le nombre de messages qu'ils cachent. Une sourdine temporaire arrivée à échéance réaffiche ses messages en moins d'une minute. Votre choix est conservé dans ce navigateur seulement (un second onglet le reprend au rechargement). Quand les filtres laissent une page presque vide, le Fil charge seul des pages plus anciennes, cinq au plus à la fois. Les messages d'un canal en attente ne chargent jamais de médias extérieurs comme les GIF.

- **Apprise, quatrième moyen de notification.** À côté du push, de l'e-mail et du webhook, **Réglages → Notifications** a désormais une colonne et une destination **Apprise** : ntfy, Telegram, Discord, Home Assistant et les autres services Apprise examinés, une URL par ligne, avec quatre exemples et le lien vers le wiki Apprise. Par défaut seul le texte du canal Public et des canaux `#` sort ; les messages directs et les salons privés partent en titre seul (« Message de Léa »), et vous pouvez choisir *Toujours* ou *Jamais*. Les mentions comme `@everyone` sont neutralisées et le format Markdown échappe le texte des messages. Les URL sont des secrets en écriture seule (affichées `********`, absentes de la sauvegarde JSON), vérifiées à l'enregistrement et avant chaque envoi ; un réseau privé demande l'autorisation habituelle. Chaque URL est envoyée et retentée séparément (5 s puis 30 s, seulement quand le service est en panne ou demande de ralentir), au plus un envoi toutes les 2 secondes, un seul « +N évènements » au-delà de 20 en attente, et les nouveaux contacts plafonnés à 10 par heure. Les contacts bloqués et les salons en sourdine n'atteignent jamais Apprise, ni le fanout Apprise. Toute modification d'une destination de notification envoie désormais une notification push « Destination de notification modifiée » à chaque navigateur abonné. Le dernier échec figure dans `/api/health` (`notification_statuses`).

- **Une page Fil réunit tous les messages dans une seule chronologie.** Le nouvel outil **Fil** (rail, Outils, palette de recherche ou `#feed`) liste les messages de tous vos canaux et messages directs, le plus récent en bas, en direct à mesure qu'ils arrivent. Il suit le dernier message tant que vous restez en bas ; si vous remontez, un bouton **Suivre** compte ce qui est arrivé depuis et vous y ramène. En remontant, les messages plus anciens se chargent depuis l'historique, 200 à la fois, jusqu'au début. Cette première version affiche l'heure, la conversation et le texte ; les noms, les filtres et l'intégration du journal de contrôle viendront ensuite. Le journal de contrôle est inchangé, et un rail que vous aviez arrangé ne reçoit pas la nouvelle entrée : ajoutez-la depuis le menu du rail.
- **Le Fil reste complet et montre où vous en étiez.** Les messages qui tombent au milieu de la chronologie (un déchiffrement historique, un message que vous envoyez) prennent leur place sans déplacer ce que vous lisez ; envoyer un message vous ramène en bas, tandis que les anciens messages tout juste déchiffrés ne comptent jamais comme nouveaux. Les accusés de réception, suppressions et renommages mettent les lignes à jour sur place. Après une reconnexion, ou quand un déchiffrement historique se termine, le Fil récupère auprès du serveur ce qu'il a manqué (une suppression faite hors ligne n'apparaît qu'au rechargement). Une ligne **Nouveau depuis votre dernière visite** marque le premier message arrivé après votre dernière ouverture du Fil, par navigateur.
- **Les messages du Fil s'ouvrent et offrent des actions.** Cliquez sur un message (ou appuyez sur Entrée) pour ouvrir sa conversation à ce message, mis en évidence ; le Fil est quitté, et le bouton retour d'un téléphone y ramène. Le nom du canal ou du contact ouvre la conversation à son dernier message, et un expéditeur dont la clé complète est connue ouvre ses détails de contact. Le bouton **...** de chaque message (appui long sur écran tactile, clic droit ou touche menu ailleurs) propose *Ouvrir dans la conversation*, *Copier le texte* et *Détails du contact* ; *Copier le texte* est masqué quand le navigateur n'offre pas de presse-papiers, comme en HTTP simple sur un réseau local.
- **`meshloom-admin`, une commande pour récupérer l'accès depuis la machine.** Elle réinitialise un mot de passe, crée un administrateur, émet le code d'installation, liste, désactive et réactive des utilisateurs et déconnecte des sessions, en modifiant directement la base : un Meshloom en cours d'exécution n'a pas besoin d'être redémarré. Elle est fournie dans le paquet Linux (`sudo meshloom-admin`), dans l'image Docker et dans l'add-on Home Assistant. Dans le paquet, elle refuse un `/etc/meshloom/meshloom.env` que quelqu'un d'autre que root pourrait modifier. La commande exacte pour chaque mode d'installation est dans la nouvelle page « Récupérer l'accès ».
- **Réglages du partage de liens et de fichiers.** `GET`/`PATCH /api/settings` portent désormais `share_enabled` (le consentement au service de partage, désactivé par défaut), `share_shorten_mode` (`auto`, `always` ou `never` ; `auto` par défaut) et `share_preview_policy` (`always`, `private` ou `never` ; `private` par défaut) ; une valeur inconnue est refusée par une 422. Une sauvegarde restaure les deux comportements mais jamais le consentement, et la seed d'identité de l'instance créée plus tard ne quitte jamais le serveur.
- **Le client du service de partage est en place, désactivé par défaut.** Meshloom peut désormais dialoguer avec le service de partage de liens et de fichiers (`meshl.ink`) dès qu’un administrateur active le partage. Tant que le partage est désactivé, Meshloom ne s’y connecte jamais, pas même pour une résolution DNS. Activé, Meshloom ne contacte que cet hôte, en HTTPS, refuse d’être redirigé ailleurs et n’envoie jamais vos clés de canal ni la clé de votre radio. Il ménage le service : les aperçus et téléchargements de liens sont plafonnés par minute, un lien absent ou expiré est mémorisé 10 minutes, trois échecs de suite suspendent tout appel pendant 5 minutes, et un bannissement arrête les envois pendant 12 heures (ou jusqu’à un changement d’un réglage de partage ou un redémarrage). Désactiver le partage interrompt aussitôt les requêtes déjà en cours. Les erreurs reviennent avec un motif traduit (service indisponible, quota atteint, lien refusé…) ; une page d’erreur de l’hôte du service compte comme « indisponible ».
- **Routes locales pour les liens courts.** `POST /api/share/links` raccourcit une URL `http(s)` via le service de partage (membres et administrateurs), `GET /api/share/status` indique si le partage est activé, l’hôte du service, si la clé de l’instance existe, si le chiffrement des messages directs est disponible et si le service a banni ce Meshloom (aucune requête n’est faite), et `GET /api/share/{id}/meta` dit ce qu’est un lien sans le suivre (type, hôte cible, expiration ; une bonne réponse est mémorisée 10 minutes). Une URL avec mot de passe, adresse IP ou nom d’hôte local est refusée avant tout envoi. Tant que le partage est désactivé, elles répondent `share_disabled` sans aucune connexion. Une écriture que le navigateur déclare venir d’un autre site est refusée (`share_cross_site`).
- **Préparation des extensions.** La base de données contient désormais des tables pour les instances d'extensions, leur stockage et leurs réglages secrets, ainsi qu'un consentement au Store, désactivé. Rien ne s'en sert encore et les robots existants ne changent pas. Les réglages secrets des extensions ne seront jamais dans une sauvegarde ni dans le téléchargement de la base.
- **Les extensions tournent dans des processus surveillés.** Chaque instance d'extension activée tourne dans son propre processus, avec des budgets de temps et de processeur : un événement qui dure plus de 30 secondes est annulé, et une extension qui ignore l'annulation est arrêtée puis relancée après une pause croissante ; après cinq échecs de suite, elle reste arrêtée jusqu'à ce que vous la relanciez. Une extension qui envoie à Meshloom un message trop long est arrêtée puis relancée de la même façon. Les événements en attente d'une extension occupée sont limités à 100 (les plus anciens sont abandonnés et comptés). Le nombre d'extensions actives est plafonné selon la mémoire de la machine. `MESHCORE_DISABLE_PLUGINS=true` garde toutes les extensions arrêtées ; sous Windows, le système d'extensions est désactivé. Rien n'installe encore d'extension, et les robots existants ne changent pas.
- **`python -m meshloom_plugin.dev check <dossier>` vérifie une extension avant de la publier.** La commande applique les mêmes règles de manifeste et de paquet que le serveur (mêmes messages), relit statiquement le code de l'extension, puis la lance dans le bac à sable, lui envoie `plugin.init` et un message de test, et affiche ce qu'elle a tenté d'envoyer (rien ne quitte votre machine). Elle sort en erreur au moindre problème et écrit du JSON avec `--json`, pour la CI.
- **Les extensions tournent derrière des murs du système.** Sous Linux, chaque processus d’extension tourne dans un bac à sable bubblewrap (ni base de données, ni réseau, ni vue sur le serveur ; le paquet Linux recommande désormais `bubblewrap` et les images Raspberry Pi l’incluent), et dans l’image Docker et l’add-on Home Assistant chaque copie d’extension tourne sous son propre compte (voir Sécurité). À ce niveau, Meshloom passe au démarrage son dossier de données en `0711` et ses fichiers en `0600` : ouvrez la base depuis l’hôte en root. Si aucun des deux ne fonctionne, les extensions tournent en « hooks seulement » et le journal du serveur l’indique à chaque démarrage. Si l’isolation ne peut pas être mise en place, aucune extension ne démarre et `/api/health` indique `plugins_disabled_source: "isolation"`. Le niveau figure dans `/api/health` (`plugins_isolation`). Les bots de la 4.18 ne changent pas.
- **Une extension ne fait que ce que vous avez autorisé.** Une extension demande tout à Meshloom, qui vérifie chaque demande d'après les permissions qui lui ont été accordées : lire les messages (par défaut les canaux publics seulement ; canaux privés, messages privés et vos propres envois seulement s'ils sont autorisés), envoyer (par défaut seulement des réponses dans la conversation à laquelle elle répond), les contacts, les canaux, le stockage (5 Mo) et les minuteurs (au moins 10 secondes). Une extension envoie par défaut au plus 30 messages par heure et 200 par jour (60 et 500 au maximum), comptés même après un redémarrage, et chaque message automatique, de bot ou d'extension, attend 2 secondes après le précédent. Une extension ne reçoit jamais ses propres messages, sauf si elle a le droit de lire vos envois. Rien n'installe encore d'extension, et les robots existants ne changent pas.
- **Les extensions ensemble ne peuvent pas inonder le maillage.** En plus des limites propres à chaque extension, Meshloom plafonne désormais toutes les extensions ensemble à 120 messages par heure, chaque canal à 20 par heure et chaque contact à 10 par heure (elles pourront être baissées plus tard, jamais relevées ; le réglage arrive avec l'écran des réglages des extensions), et coupe les boucles entre répondeurs automatiques : une copie d'extension répond au plus une fois toutes les 30 secondes au même expéditeur et ignore un message arrivé moins de 5 secondes après son propre envoi vers la même conversation. Deux extensions « écho », même sur deux Meshloom différents, sont réduites au silence après quelques messages ; un envoi refusé est consigné avec la garde qui s'est déclenchée. Les compteurs survivent à un redémarrage. Aucune extension ne s'installe encore, et les bots existants ne changent pas.
- **Une extension ne joint que les serveurs internet que vous avez autorisés.** Une extension qui a la permission internet demande à Meshloom de faire chaque requête, et Meshloom ne l'envoie qu'aux noms de serveurs exacts que l'extension liste et que vous avez acceptés, en HTTPS, jamais vers votre réseau local (quelle que soit l'adresse du nom), et suit au plus trois redirections, chacune vers un autre nom listé. Les mots de passe ou clés d'API d'une extension ne suivent jamais une redirection vers un autre serveur, et la requête ne porte aucun cookie ni rien de propre à Meshloom. Les réponses sont coupées à 1 Mo, une requête envoie au plus 256 Ko et dure au plus 10 secondes, et chaque copie d'extension fait au plus 60 requêtes par heure. Les serveurs `meshloom.app` sont réservés aux extensions de Meshloom. Le journal montre chaque requête sans la partie de l'adresse après `?`, comme désormais la ligne de requête HTTP que Meshloom journalise pour chaque requête sortante (jetons de webhook compris). Joindre un appareil de votre réseau local, accepté par son nom, viendra plus tard. Rien n'installe encore d'extension.
- **Le Fil affiche le trafic de contrôle et une facette Nœuds.** La puce **Contrôle** est désormais active par défaut : les paquets Request, Response, requête anonyme et données de groupe apparaissent dans la chronologie entre les messages, par ordre d'arrivée, avec leur type, l'expéditeur → le destinataire, les données de groupe déchiffrées ou l'enveloppe verrouillée (destinataire et source) ; un clic ouvre l'inspecteur de paquet. Un badge **Apparié** sur une requête ou une réponse défile jusqu'à sa jumelle quand elle est affichée. Contrairement au tampon de paquets bruts, ces lignes sont conservées quand la connexion tombe puis revient, et ne sont pas ajoutées deux fois. La facette **Nœuds** (une colonne sur ordinateur, un bouton qui ouvre un panneau sur téléphone) liste les répéteurs et salons, les canaux, les paquets qui nous sont adressés et les non résolus, avec leur nombre ; en choisir un restreint les lignes de contrôle à celui-ci, et un canal restreint aussi les messages à ce canal. Les requêtes et requêtes anonymes des autres nœuds ne sont toujours jamais déchiffrées.

- **Le journal de contrôle devient un préréglage du Fil.** **Contrôle** ouvre le Fil avec seulement la source *Contrôle* activée et la facette Nœuds (ouverte sur téléphone), à la place de l'ancienne page Journal de contrôle, qui disparaît. `#control`, les signets, les URL de kiosque et une entrée **Contrôle** de la barre ou une dernière page consultée mémorisée continuent de fonctionner et arrivent sur ce préréglage ; la barre d'adresse affiche toujours `#control`. Les changements de puces dans le préréglage ne sont pas mémorisés : le Fil habituel garde vos filtres. **Contrôle** n'est plus dans la barre d'icônes par défaut ni dans la liste Outils du téléphone (utilisez **Fil**) ; il reste disponible dans **Réglages → Navigation** et dans la palette de recherche, sous le nom *Contrôle (préréglage du fil)*.
- **Une page de connexion et une page de première configuration.** Meshloom s'ouvre maintenant sur la page qui convient : sur une installation neuve, **Créer le compte administrateur** (nom d'utilisateur, mot de passe d'au moins 12 caractères avec une indication de longueur, nom affiché facultatif et langue de l'interface) ; sur une instance qui contenait déjà des données, la même page demande aussi le **code d'installation** et dit où le trouver (journal Docker, `journalctl -u meshloom`, onglet Journal du module, ou `meshloom-admin setup-code`) ; sinon une page de connexion, qui prévient quand la page n'est pas servie en HTTPS. Rien de l'application ne se charge avant, et une fois le compte créé ou la connexion faite elle s'ouvre sans rechargement. Quand une session se termine alors que Meshloom est ouvert, une fenêtre demande de se reconnecter et vous reprenez exactement où vous en étiez ; la connexion en direct se rétablit seule. Votre nom figure dans un bouton de compte (bas de la barre latérale, haut des **Réglages** sur téléphone) avec **Se déconnecter**. Les instances protégées par l'authentification Basic qui s'authentifient déjà ne voient aucune page de connexion.
- **Home Assistant vous connecte à l'add-on.** Ouvert depuis la barre latérale de Home Assistant, Meshloom utilise désormais votre utilisateur Home Assistant : pas de mot de passe Meshloom, pas de page de création de compte et pas de code d'installation, y compris juste après une mise à jour depuis la 4.18. Le premier utilisateur Home Assistant qui l'ouvre devient l'administrateur ; les suivants reçoivent le rôle Membre. Meshloom ne fait confiance à cette identité que lorsqu'elle vient du Supervisor de Home Assistant, n'y pose aucun cookie à lui (tous les add-ons partagent l'adresse de Home Assistant), et un port 8000 publié demande toujours un compte et un mot de passe Meshloom. Ouvert par la barre latérale sans utilisateur Home Assistant, il explique comment entrer au lieu d'afficher un formulaire de mot de passe. Les options `basic_auth_username` et `basic_auth_password` de l'add-on sont dépréciées et seront retirées à la prochaine version : remplies, elles deviennent une fois un compte Meshloom, au premier démarrage tant qu'aucun compte n'existe. La liste de ces comptes et le changement de leur rôle dans les réglages arrivent avec la section Utilisateurs et accès.
- **Les en-têtes transmis ne sont crus que depuis les proxies de confiance que vous listez.** L'image Docker, le paquet Linux, l'add-on, l'installeur de service et les commandes documentées démarrent désormais le serveur avec `--no-proxy-headers`, et Meshloom lit lui-même `X-Forwarded-For` et `X-Forwarded-Proto`, uniquement depuis les adresses du nouveau réglage **Réglages → Configuration locale → Proxies de confiance** (vide par défaut, `127.0.0.1` compris seulement si vous l'ajoutez). Avant, un programme sur la même machine pouvait se faire passer pour n'importe quelle adresse, dont celle du Supervisor de Home Assistant. Depuis un proxy listé, les délais de connexion, le journal d'audit et les sessions reçoivent l'adresse du visiteur (la plus à droite ajoutée par la chaîne de proxies, jamais une valeur écrite par le visiteur), et `X-Forwarded-Proto: https` donne le cookie de session sécurisé. **Derrière un proxy inverse, listez sa propre adresse** (jamais un réseau Docker ni sa passerelle, qui feraient aussi confiance aux clients atteignant un port publié par elle ; la page des réglages signale une telle entrée) : sinon tous les visiteurs ont l'adresse du proxy et, en HTTPS, le cookie de session est le cookie simple, ce que la page de connexion et les réglages signalent désormais. Des exemples pour Caddy, nginx et Traefik sont dans la page HTTPS de la documentation. Si vous lancez Meshloom avec votre propre commande, ajoutez-y `--no-proxy-headers`.
- **Une section Partage dans les réglages, désactivée par défaut.** **Réglages → Partage : liens et fichiers** indique si le partage est activé, si l'identité de partage de ce Meshloom est prête et si les fichiers chiffrés pour les messages privés sont disponibles. Activer le partage ouvre toujours une fenêtre de consentement qui liste exactement ce qui quitte ce Meshloom à l'envoi (le fichier ou l'URL, la clé publique de partage, l'adresse IP) et à la lecture (l'identifiant de chaque objet affiché, l'adresse IP), qui exploite le service, combien de temps les données sont conservées, et une case obligatoire pour accepter les conditions. Annuler le laisse désactivé. La section règle aussi quand les liens sont raccourcis (automatique, toujours, jamais) et quand les aperçus sont récupérés.
- **Les liens courts indiquent où ils mènent.** Quand le partage est activé, un lien court dans un message (`meshl.ink/…`) affiche une petite indication `→ example.org` au survol ou au focus, demandée une seule fois par lien et gardée dix minutes. Rien n'est demandé à l'affichage du message, et rien du tout quand le partage est désactivé. Une conversation dont le dernier message est un lien court affiche désormais `🔗` suivi de sa légende (ou « Lien ») dans la liste.
- **Les images partagées s’affichent dans la conversation.** Quand le partage est activé, un message envoyé sous la forme `📷 meshl.ink/…` affiche l’image sous son texte (240×240 au plus, un clic l’ouvre en grand ; au-delà de 8 Mio, une carte **Télécharger l'image** à la place). **Réglages → Partage : liens et fichiers → Aperçus des images et liens partagés** décide si elle se charge seule : *Toujours*, *Conversations privées seulement* (messages directs et canaux dont la clé n’est pas dérivée du nom) ou *Jamais* ; sinon un bouton **Toucher pour charger l'image**. Tant que vous n’avez pas touché, rien ne quitte votre navigateur ni ce Meshloom, pas même une question sur l’image, et survoler le lien ne demande rien non plus. Une image d’un expéditeur bloqué, ou affichée avant le chargement des réglages, ne se charge jamais seule ; l’écran d’affichage du Fil montre un simple libellé *Image non chargée* au lieu du bouton. Une image expirée ou supprimée le dit. Les membres et administrateurs peuvent **Signaler** une image au service depuis sa carte (8 motifs, détails facultatifs, 5 signalements par jour pour ce Meshloom ; `POST /api/share/{id}/report`, signé avec la clé de ce Meshloom). La liste des conversations affiche `📷 Image` ou `🔒 Fichier chiffré`, jamais l’adresse. `GET /api/channels` renvoie `key_derived`, et `share_conversation_id`, l’identifiant opaque par lequel un envoi chiffré désigne un canal (`X-Share-Conversation-Id`), pour que la clé du canal ne revienne jamais du navigateur. Anglais et français.
- **Un bouton Raccourcir dans le composeur de messages.** Quand le partage est activé et qu'un message dépasse la limite de taille à cause d'une longue adresse web, un bouton **Raccourcir** apparaît à côté du compteur d'octets : il remplace chaque longue adresse de la zone de texte par un lien court `meshl.ink`, affiche la nouvelle taille et propose **Annuler**. Rien n'est envoyé par ce geste, et Envoyer refuse toujours un message qui reste trop long. Le réglage du partage décide du comportement : *automatique* (le bouton), *toujours* (Envoyer raccourcit d'abord les adresses, vérifie la taille puis envoie) ou *jamais*. Les liens courts, les messages `g:`/`m:`/`r:` et les textes sans longue adresse sont laissés tels quels ; si le service refuse, votre texte reste intact. Tant que le partage est désactivé, rien n'est demandé.
- **Mes envois, et un bouton Vider le cache, dans la section Partage.** **Réglages → Partage : liens et fichiers → Mes envois** liste les liens et fichiers partagés depuis ce Meshloom (nature, type de fichier ou site du lien, taille, expiration, état), avec **Actualiser** et **Afficher plus**. **Supprimer** demande une confirmation, puis retire l’objet du service de partage et du cache de ce Meshloom : il cesse aussitôt de se charger. Un objet bloqué par le service est grisé, dit pourquoi (spam, logiciel malveillant…) et renvoie vers la page de contact du service ; il ne peut pas être supprimé. Les membres voient cette liste (et seulement elle) dans la section ; les administrateurs ont aussi **Vider le cache**, qui indique l’espace libéré. Tant que le partage est désactivé, il n’y a pas de liste et la section explique pourquoi : Meshloom ne contacte pas le service. `GET /api/share/mine` et `DELETE /api/share/{id}` (membres et administrateurs ; les rôles Lecteur et Kiosque sont refusés). Anglais et français.
- **Une page Liens courts dans la documentation.** La nouvelle page **Liens courts** (Aller plus loin) explique de bout en bout le service de partage optionnel : il est désactivé par défaut et Meshloom ne s’y connecte jamais, pas même par une résolution DNS, tant qu’un administrateur n’a pas accepté la fenêtre de consentement ; ce qui sort quand vous raccourcissez un lien et quand vous en survolez un ; la clé de partage, qui n’est jamais celle de la radio et que ni les sauvegardes ni les restaurations ne transportent (une restauration n’active jamais le partage et crée toujours une nouvelle clé) ; les trois modes de raccourcissement et le bouton Raccourcir ; les adresses que Meshloom n’envoie jamais (identifiants, adresses IP, noms locaux) ; la page d’aperçu `+` et la page de signalement du service ; et ce que signifie un bannissement pour **cette instance**. **Messages**, **Sécurité** (« Ce qui sort de votre machine » et « La clé de partage ») et **Un réseau de confiance** la mentionnent désormais. Signaler un lien court depuis Meshloom n’est pas dans cette version (la page d’aperçu du service a un bouton **Signaler**).
- **Les administrateurs gèrent les comptes par l'API.** `GET`/`POST /api/auth/users`, `PATCH`/`DELETE /api/auth/users/{id}`, `POST /api/auth/users/{id}/password` et `DELETE /api/auth/users/{id}/sessions` (administrateurs seulement) listent les comptes, ceux de Home Assistant compris avec leur origine, en créent un avec l'un des quatre rôles (un mot de passe initial pour tout rôle sauf `kiosk`, à changer à la première connexion), changent son rôle ou son nom, le désactivent, le réactivent ou le suppriment, fixent son mot de passe et le déconnectent partout. Le dernier administrateur actif ne peut jamais être rétrogradé, désactivé ni supprimé (`auth_last_admin`). Un changement de rôle, une désactivation, une suppression ou un mot de passe fixé par un administrateur déconnecte aussitôt le compte, connexions en direct comprises, et oublie ses navigateurs mémorisés. Un compte Home Assistant prend n'importe quel rôle, `kiosk` compris, mais jamais de mot de passe ; le rôle donné aux nouveaux utilisateurs Home Assistant est `ingress_default_role` dans `GET`/`PATCH /api/auth/settings`, et seul le tout premier utilisateur Home Assistant devient administrateur : le supprimer ne le refait pas administrateur. Chaque changement est inscrit au journal d'audit, refus compris. L'écran des réglages correspondant arrive plus tard.
- **Un mot de passe fixé par un administrateur doit d'abord être changé.** Un compte marqué ainsi (par un administrateur, ou `meshloom-admin reset-password --must-change`) peut seulement lire son profil, changer son mot de passe avec `PATCH /api/auth/me` ou se déconnecter tant qu'il ne l'a pas fait ; tout le reste répond `auth_password_change_required`, et `GET /api/auth/session` indique `must_change_password`. Changer son propre mot de passe déconnecte ses autres navigateurs, connexions en direct comprises ; un mot de passe actuel erroné passe par les mêmes délais que la page de connexion.

- **L’interface ne propose que ce que votre rôle permet.** Un lecteur ne voit ni zone de saisie, ni bouton de nouvelle conversation, ni épingle, favori, sourdine ou suppression, et seulement la **Configuration locale** dans les Réglages. Un membre peut envoyer et gérer canaux et contacts, mais ne voit jamais le fanout, la sauvegarde, la radio, les mises à jour ni les autres sections d’administration, ni les volets répéteurs et salons, le test du maillage ou le blocage. Un écran kiosque n’affiche que les canaux : aucun message direct (ni dans la liste, ni filtre Direct, ni dans la palette de recherche), aucune zone de saisie, aucune porte vers les Réglages ; son bouton de compte ne change que la langue et le thème. Rien n’est protégé par cela : le serveur refuse toujours toute requête hors du rôle, et une action refusée affiche un seul message traduit (« Ce compte n’a pas le droit de faire cela. ») au lieu du texte du serveur. Une lecture refusée, comme l’état des messages directs d’un kiosque, ne déclenche jamais d’erreur.
- **Les autres rôles ne demandent plus au démarrage les données réservées aux administrateurs.** Membres, lecteurs et kiosques ne demandent plus la configuration de la radio, les réglages de l’application, l’état des mises à jour, Community ni les préférences push, que le serveur ne donne qu’aux administrateurs (la page collectait des refus). Limite connue : ces rôles utilisent le thème et la navigation par défaut, et les expéditeurs et noms bloqués ne sont pas masqués pour eux.
- **Une page de changement de mot de passe obligatoire.** Quand un administrateur définit votre mot de passe, Meshloom affiche une page demandant le mot de passe actuel et un nouveau (avec les règles) à la place de l’application, puis continue une fois le changement fait. Toute requête refusée avec « changement de mot de passe requis » vous y amène aussi.
- **Accès à distance par vos propres moyens (documentation).** Une nouvelle page, [Accès à distance par vos propres moyens](docs/user/fr/deep/remote-self-hosted.md), explique comment joindre Meshloom de l’extérieur avec votre propre Cloudflare Tunnel (et Access devant), Tailscale ou WireGuard, ou un reverse-proxy Caddy, nginx ou Traefik, et ce qu’il faut déclarer dans Meshloom : adresses publiques, proxies de confiance, mode du contrôle du Host, réseaux de confiance laissés désactivés derrière un proxy, HSTS, passkeys en https seulement, Web Push et l’add-on Home Assistant. Elle est liée depuis les pages HTTPS, Comptes et accès, Notifications et Installation.
- **Réglage de la langue des notifications.** **Réglages → Notifications → Langue des notifications** (Automatique, Français, English ; `notification_language` dans `GET`/`PATCH /api/settings`) fixe la langue des notifications par e-mail et Apprise. Automatique garde le comportement précédent (anglais seulement si la plupart des navigateurs abonnés sont en anglais, français en cas d’égalité) ; choisir une langue l’impose même si les abonnements push en utilisent une autre. Les notifications push gardent la langue de chaque navigateur. Le texte de toutes les notifications vient désormais d’un seul moteur de rendu, sans changer aucun message existant.
- **API des instances de plugins.** `/api/plugins` gère les instances de plugins : `GET /api/plugins` et `GET /api/plugins/bundled` (membres et administrateurs ; la configuration, le code et les erreurs ne sont visibles que des administrateurs), `POST /api/plugins/install` (un plugin fourni, refusé avec `consent_required` tant que les permissions accordées ne couvrent pas ce que le plugin demande ; un champ de configuration obligatoire manquant crée l’instance à l’arrêt avec `attention.type = config_required`), `POST /api/plugins` (un plugin local depuis un modèle), `PATCH /api/plugins/{id}` (activer ou désactiver démarre ou arrête son processus ; libellé, configuration vérifiée contre le schéma du plugin, portée, permissions, quotas jamais au-dessus des plafonds, mise à jour automatique, ordre), `PUT /api/plugins/{id}/source` (le code d’un plugin local, syntaxe vérifiée, redémarrage), `POST .../restart`, `POST .../check`, `POST .../test` (un message synthétique : les réponses sont capturées et jamais envoyées à la radio), `GET .../logs`, `GET .../diagnostic` et `DELETE /api/plugins/{id}?keep_data=`. Les champs secrets de la configuration sont en écriture seule et masqués dans les journaux, diagnostics et résultats de test, les journaux des plugins ne font pas partie de `/api/debug`, et l’événement WebSocket `plugin_status` (administrateurs seulement) porte désormais la raison `attention`. Les plugins fournis eux-mêmes arrivent avec un prochain changement : rien n’est encore installable.
- **Transports natifs e-mail, webhook et Apprise pour les canaux de notification.** Un canal e-mail accepte plusieurs destinataires (un par ligne) et un Cc facultatif (chaque adresse reçoit son propre message), et rédige son message dans la langue du canal ; quand le canal retient le texte d'un message, le sujet est un « Nouveau message » neutre. Un canal webhook envoie désormais `X-Webhook-Id` (identique à chaque nouvelle tentative, pour que le récepteur écarte un doublon) et `X-Webhook-Timestamp` à côté de la signature inchangée, et peut désactiver la vérification du certificat, uniquement pour une destination sur un réseau privé, et un identifiant e-mail n'est jamais envoyé sans chiffrement hors de ce réseau. Un canal Apprise envoie chaque URL séparément : une URL en échec est signalée par sa ligne et ne fait jamais recevoir l'alerte deux fois aux autres. Les destinations existantes envoient aussi les nouveaux en-têtes du webhook.

#### Modifications

- **Rupture : les bots Python sont retirés et convertis en extensions locales.** Le type de fanout `bot` n’existe plus. Au premier démarrage de la 5.0.0 (migration 122), chaque bot devient une extension locale à son nom, avec son code, ses conversations et son interrupteur conservés. Une extension convertie ne démarre jamais toute seule : elle est à examiner (*Permissions à revoir*) et reste éteinte tant qu’un administrateur n’a pas accepté ce qu’elle peut faire (lire tous les canaux, les messages directs et les messages envoyés ; répondre dans la conversation du message auquel elle répond) dans **Réglages → Extensions**. Les bots d’origine sont copiés dans la table `fanout_configs_bots_archive` (hors sauvegardes et `/api/debug`, purgée en 5.2.0). Les bots ne tournent plus dans le processus du serveur : plus aucun code utilisateur n’y est exécuté par `exec`. Voir [Extensions](docs/user/fr/deep/plugins.md#les-bots-de-la-418-et-avant).
- **Rupture : les routes des bots répondent ou disparaissent.** `POST /api/fanout` avec `type: "bot"` et `PATCH /api/fanout/{id}` sur un bot archivé répondent `410` avec le code `bot_type_retired`. `POST /api/fanout/bots/disable-until-restart` est supprimée (404) : utilisez la pause globale de **Réglages → Extensions** ou `MESHCORE_DISABLE_PLUGINS`. Les actions d’audit `bot.created`, `bot.changed`, `bot.deleted` et `bots.disabled` ne sont plus écrites (les anciennes entrées restent visibles).
- **Rupture : `MESHCORE_DISABLE_BOTS` est retirée.** Elle ne verrouille plus rien. Si elle est encore posée, Meshloom journalise au démarrage un avertissement qui nomme `MESHCORE_DISABLE_PLUGINS`, le verrou de toutes les extensions (voir [Environnement](docs/user/fr/deep/environment.md)) ; la migration 122 la lit une dernière fois et, si elle vaut `true`, enregistre toutes les extensions converties éteintes. Le paquet Linux et l’image Raspberry Pi ne posent plus aucun verrou dans `meshloom.env` (un `MESHCORE_DISABLE_PLUGINS` commenté s’y trouve à la place) ; `.env.example`, les fichiers Compose et la documentation suivent.
- **Rupture : l’option `disable_bots` de l’add-on Home Assistant est supprimée.** Utilisez `disable_plugins`. Si un `disable_bots: true` résiduel parvient à l’add-on, il est transmis une fois à la migration 122 (les extensions converties arrivent éteintes, comme avec le paquet) et le journal de l’add-on dit que l’option n’existe plus.
- **Obsolète : `bots_disabled` et `bots_disabled_source` dans `/api/health`.** Ils restent une version et suivent désormais le verrou et la pause des extensions (`env` ou `paused` ; `until_restart` n’apparaît plus). `/api/health` gagne `converted_bots` (`{converted, attention}`, `null` une fois l’avis fermé par un administrateur avec `POST /api/plugins/converted-bots/acknowledge`). `/api/debug` ne liste plus `disable_bots`.
- **Les sauvegardes convertissent les bots.** Les nouvelles sauvegardes ne contiennent aucun bot. Un bot d’une sauvegarde faite avant la 5.0.0 est restauré comme extension locale, éteinte et en attente de relecture ; le résumé les nomme (`bots_converted`), et le cas « bots ignorés quand les bots sont désactivés » disparaît.
- **L’avertissement de sécurité sur les bots est supprimé.** La boîte plein écran « L’exécution non protégée de bots est activée » et son bouton **Désactiver les bots jusqu’au redémarrage du serveur** n’existent plus, avec les bots.
- **Revenir en arrière après cette mise à jour.** Revenir à une version antérieure à la 5.0 relit les anciens réglages de notification : **les canaux ajoutés après la mise à jour (ntfy, Discord, Telegram, Gotify, Home Assistant, un deuxième e-mail ou webhook) ne sont pas visibles après un retour en arrière**, et les règles que vous avez changées dans le tableau ne sont pas lues. Notez d’abord les destinations dont vous avez besoin, ou restaurez une sauvegarde faite avant la mise à jour.
- **Points d’accès de notification dépréciés.** `GET`/`PATCH /api/push/preferences`, `PUT /api/push/preferences/conversations/{key}`, `notification_destinations` dans `PATCH /api/settings` et `POST /api/settings/notification-destinations/test` répondent un en-tête `Deprecation` et seront retirés en 5.2.0 ; utilisez `/api/notifications`. Ils continuent de fonctionner et sont reflétés dans les règles pour le push, l’e-mail, le webhook et Apprise. Un `PATCH` de `config`, `allow_private_network` ou `verify_tls`, ou un `DELETE`, sur un canal `legacy:` répond toujours `409 legacy_managed` : ces trois destinations se modifient avec leur propre formulaire.
- **Les mentions venues du mesh sont cassées aussi dans les canaux en texte brut.** L’échappement commun introduit avec Discord s’applique à toute notification ntfy, Discord, Apprise et préréglage (Telegram, Gotify, Home Assistant), en texte brut comme en Markdown (les canaux en texte brut étaient décrits comme inchangés) : `@everyone`, `@here`, `@channel`, `@all`, `@room`, `<@`, `<!` et désormais `<#` reçoivent un caractère invisible, et `@everyone` ou `@here` sont cassés même si une lettre suit. Rien ne change dans la lecture d’un message, sauf pour ces séquences.
- **Expéditeurs bloqués et salons en sourdine ne notifient plus, sur aucun canal.** Un message d’un contact ou d’un nom de vos listes de blocage ne produit plus de notification sur aucun canal (Web Push, e-mail, webhook, Apprise et les canaux à venir), alors que la 4.18.x ne le retenait que pour Apprise ; un salon en sourdine (jusqu’à la fin de la sourdine) reste silencieux sur tous les canaux, exceptions comprises. Un événement marqué *toujours livrer* passe quand même. Rien ne change tant que vous ne bloquez personne et ne mettez aucun salon en sourdine. Les listes de blocage sont gardées en mémoire : un message ne coûte aucune lecture de base de données de plus.
- **Les notifications sont livrées depuis les règles.** Tous les producteurs de notifications (nouveaux messages, contact vu pour la première fois, canal trouvé, alertes de télémétrie, nouvelles versions, radio hors ligne ou rétablie, démarrage) confient désormais leur évènement au répartiteur de notifications, et ce sont les règles de `/api/notifications/rules` qui choisissent les canaux : l’exception d’une conversation pour un canal l’emporte, un message direct suit `message.dm`, un canal Public ou `#` suit `message.channel`, et un canal à clé privée ne notifie jamais sans exception. Rien ne change si vous ne touchez pas aux nouveaux réglages : les préférences de Réglages → Notifications et de la cloche sont conservées dans les règles et le résultat est le même (les textes des notifications sont strictement identiques). Ce que la nouvelle API change dans les règles est recopié, de sorte que `/api/push/preferences` (déprécié) le montre encore. Les règles d’alerte de télémétrie ne stockent plus de champ `channels` (une ancienne requête qui en envoie un est acceptée et ignorée : les règles de notification choisissent les canaux).

- **Les notifications passent par une file par destination.** Push, e-mail, webhook et Apprise ont chacun leur file et envoient une notification à la fois : un service lent ou en panne ne retarde plus les autres ni le push. L’e-mail envoie au plus une notification toutes les 10 secondes. Le regroupement « +N évènements » et le plafond des nouveaux contacts, jusqu’ici propres à Apprise, s’appliquent à toutes les destinations, push, e-mail et webhook compris : au-delà de 20 en attente pour une destination, les suivantes deviennent un seul « +N évènements » (jamais pour les urgentes), et les nouveaux contacts notifient au plus 10 fois par heure et par destination, puis un seul « +N nouveaux contacts ». Un webhook en panne, trop lent ou qui demande de ralentir est désormais retenté après 5 s puis 30 s (jamais quand il refuse la requête) ; une notification de message qui échoue encore est abandonnée, une radio hors ligne ou une alerte de télémétrie est retentée une fois 5 minutes plus tard quand le service était injoignable, jamais après un refus. `/api/health` donne désormais l’état de chaque destination sous `notification_statuses`, par identifiant (`builtin:push`, `legacy:email`, `legacy:webhook`, `legacy:apprise`), avec `status`, le code de la dernière erreur et le dernier succès, et la connexion en direct envoie `health` quand il change ; les anciennes entrées `notification_statuses.webhook` et `.apprise` disparaissent. Les notifications en échec, regroupées, abandonnées et annulées sont journalisées sans leur texte ; les envois réussis sont seulement comptés. Les notifications encore en attente à l’arrêt ne partent pas après le redémarrage.
- **Les destinations de notification deviennent des canaux (aucun changement visible pour l’instant).** À la mise à jour, Meshloom transforme les préférences Web Push, e-mail, webhook et Apprise en canaux de notification (`builtin:push`, `legacy:email`, `legacy:webhook`, `legacy:apprise`) et vos choix par événement et par conversation en règles de notification, rangés dans de nouvelles tables et `notification_rules`. Rien n’est perdu et l’envoi ne change pas pour l’instant : il suit encore les anciens réglages jusqu’à l’arrivée du nouveau distributeur, et les anciennes colonnes sont conservées. Les secrets des canaux sont en écriture seule : aucune API ne les renvoie. Les contrôles de destination du garde de sortie s’appliquent à chaque nouveau canal.
- **La page d’aide « Récupérer l’accès » devient « Comptes et accès », et les pages de sécurité décrivent la 5.0.** Une seule page couvre les rôles, les sessions, les réseaux de confiance, les jetons, la double authentification et les passkeys, Home Assistant, l’écran kiosque, la fenêtre de la connexion Basic de la 4.x (jusqu’à la 5.2), le journal d’audit et `meshloom-admin` ; « Un réseau de confiance », « Sécurité », les pages d’installation, le README et `SECURITY.md` ne disent plus qu’il n’y a pas de comptes ni que le CORS est ouvert, et disent clairement que le proxy radio n’a aucune connexion.

- **`/api/health` et `/api/debug` n'exposent plus `basic_auth_enabled` ; les variables Basic retirées ne font que prévenir.** Le champ disparaît des deux réponses (l'interface lit déjà `GET /api/auth/session`). `MESHCORE_BASIC_AUTH_USERNAME` et `_PASSWORD` rejoignent les autres variables retirées : le premier démarrage sans compte en fait toujours l'administrateur, et chaque démarrage journalise un avertissement qui renvoie vers **Réglages → Utilisateurs et accès**. En définir une seule n'empêche plus Meshloom de démarrer ; rien n'est importé. `Authorization: Basic` reste accepté jusqu'à la 5.2 et journalise un avertissement de dépréciation à sa première utilisation après chaque démarrage.

- **Le déchiffrement groupé des salons affiche un message de fin traduit.** Après le déchiffrement d'anciens paquets pour plusieurs salons à la fois, la confirmation suit désormais votre langue, comme pour un seul salon. Les messages ainsi déchiffrés sont aussi marqués comme anciens sur la connexion en direct (`historical: true`, jamais envoyé aux bots ni aux webhooks), pour qu'un client puisse les distinguer des nouveaux.
- **Le téléchargement de meshcore.db ne contient plus le code PIN Bluetooth.** La copie de la base depuis **Réglages → Base de données** laisse désormais de côté le code PIN Bluetooth, comme la sauvegarde complète, ainsi que les clés avec lesquelles Meshloom s'identifiera auprès des services en ligne. Remettre une telle copie en place compte comme une restauration au prochain démarrage de Meshloom.
- **Les fichiers à restaurer sont limités à 64 Mio.** Un fichier plus gros est refusé avec une erreur claire.
- **Restaurer une ancienne sauvegarde ne laisse plus d’avertissement obsolète sur le push du réseau local.** Quand une restauration réactive le push vers le réseau local pour vos abonnements, l’avis qui demandait de le vérifier est aussi effacé.

#### Corrections

- **Les limites de notifications tiennent sous un afflux.** Les clés de dédoublonnage en attente et stockées sont désormais bornées et élaguées, le plafond global est vérifié et débité en une seule étape, et les résumés groupés comptent dans le plafond de 600 par heure.
- **Les liens Apprise mènent à la documentation actuelle.** Les liens « Formats d’URL Apprise » de Réglages → Notifications et Réglages → Fanout, ainsi que ceux de la documentation push, menaient au wiki Apprise archivé. Ils ouvrent désormais la page de syntaxe des URL Apprise sur appriseit.com.

#### Sécurité

- **Chaque copie d’extension tourne sous son propre compte, et Meshloom n’a plus besoin du droit d’arrêter n’importe quel processus.** Dans l’image Docker et l’add-on Home Assistant, chaque copie d’une extension tourne désormais sous un compte numérique qui lui est propre (40000 à 49999, gardé tant que la copie existe) au lieu du compte partagé `meshloom-plugin` : une extension ne peut plus lire les fichiers temporaires d’une autre ni l’arrêter. Supprimer une copie efface les fichiers temporaires laissés par son compte et libère le numéro, et le premier démarrage après la mise à jour nettoie ce que le compte partagé avait laissé. Avec `MESHLOOM_RUN_AS_USER`, Meshloom ne garde plus que `CAP_SETUID` et `CAP_SETGID` : `CAP_KILL` disparaît, un petit assistant lancé sous le compte de l’extension l’arrête (migration 121). La documentation du verrou de l’hôte (`MESHCORE_DISABLE_PLUGINS`, l’option `disable_plugins` de l’add-on) dit maintenant ce qu’il fait vraiment : il refuse d’installer, de modifier et de démarrer des extensions, et laisse consulter, supprimer et tout mettre en pause. En réseau hôte, une route par défaut passant par une interface VLAN ou macvlan est maintenant reconnue : les extensions peuvent y joindre le routeur du réseau local comme avec une interface simple. En français et en anglais.
- **Les extensions ne peuvent plus lancer de programme ni de fil d'exécution, même quand Meshloom tourne en root.** Le processus d'une extension installe maintenant un filtre du noyau (seccomp) qui refuse tout nouveau processus, fil et programme, en plus de ses limites existantes, que root ignorait : un Meshloom lancé en root sans le compte des extensions (donc au niveau bubblewrap ou hooks seulement) pouvait laisser une extension hostile lancer un programme. Un processus d'extension en root qui ne peut pas installer le filtre refuse désormais de démarrer. Avec `MESHLOOM_RUN_AS_USER`, l'image Docker ne garde que les deux droits nécessaires pour exécuter les extensions sous un autre compte (`CAP_SETUID`, `CAP_SETGID`) : les extensions tournent sous leur propre compte (niveau `setuid`) au lieu des hooks seulement, et leur processus abandonne ces droits avant tout code d'extension. Le paquet Linux recommande bubblewrap, et son service peut lancer au plus 512 processus et fils (`TasksMax`). L'add-on Home Assistant a une option `disable_plugins` qui empêche toute extension de démarrer.
- **Seuls les administrateurs modifient les extensions, et chaque modification est auditée.** Installer, configurer, activer, désactiver, redémarrer, tester, modifier et désinstaller une extension, ou lire son journal ou son diagnostic, demande un administrateur ; les membres voient **Réglages → Extensions** en lecture seule avec une note qui explique pourquoi, les comptes lecteur et kiosque n’ont pas cette section. Chaque modification est inscrite au journal d’audit avec son auteur et l’extension concernée (`plugin.installed`, `plugin.created`, `plugin.enabled`, `plugin.disabled`, `plugin.config_changed`, `plugin.changed`, `plugin.updated`, `plugin.removed`), avec le seul nom des champs modifiés : jamais une valeur de réglage, un secret ou le code. Être sur le réseau local ne donne aucun droit d’installer quoi que ce soit ; la page sur la confiance le dit désormais. En français et en anglais.
- **Les appareils push appartiennent à leur compte.** Chaque compte ne voit, ne renomme, ne teste et ne retire plus que les navigateurs qu'il a abonnés aux notifications push ; l'appareil d'un autre compte lui est invisible. Un administrateur voit aussi les appareils des autres comptes (étiquette, compte et service push, jamais l'adresse complète) et peut en retirer un, sans le renommer ni le tester. Un appareil n'est notifié que tant que son compte est actif et peut envoyer des messages, et supprimer un compte supprime ses appareils. Les appareils enregistrés avant cette version, et ceux restaurés depuis une sauvegarde, appartiennent au premier administrateur.
- **La connexion en direct n'envoie chaque événement qu'aux rôles autorisés à le recevoir, et jamais un événement qu'elle ne sait pas classer.** Chaque sorte d'événement en direct est désormais listée une seule fois sur le serveur avec la permission nécessaire pour le recevoir, et la liste du navigateur en est générée. Un événement d'une sorte absente de cette liste n'est envoyé à personne, administrateurs compris (il parvenait auparavant aux administrateurs). Un kiosque reçoit toujours les messages des canaux, envoyés et historiques compris, et jamais un message direct, ni qui vous en a envoyé un ni quand (les mises à jour de contacts ne portent plus cette activité, comme l'API le faisait déjà), ni les accusés de réception des messages directs. Le détail des erreurs des intégrations, des canaux de notification, du proxy radio et des plugins, les changements d'état des plugins et les notifications (broker connecté, erreurs de la radio) ne parviennent plus qu'aux administrateurs ; les autres rôles voient toujours si chaque intégration fonctionne. Les notifications d'un envoi ou d'un déchiffrement historique parviennent toujours aux rôles qui peuvent le voir. Les changements d'état des plugins n'apparaissent plus comme des événements inconnus dans la console du navigateur.
- **En-têtes de sécurité.** Meshloom envoie désormais `Strict-Transport-Security` quand on l'atteint directement en HTTPS (jamais en HTTP simple, via la barre latérale de Home Assistant ni via le relais), marque `Cache-Control: no-store` les réponses qui portent des secrets (clé privée de la radio, sauvegardes, clés des canaux), comme l'étaient déjà celles de connexion, et envoie une `Permissions-Policy` qui coupe la caméra et le micro. Voir la page HTTPS.
- **Déconnecter une session coupe aussitôt sa connexion en direct.** Se déconnecter, révoquer une session depuis la liste, changer son mot de passe (ce qui déconnecte vos autres sessions) ou dépasser 50 sessions sur un compte (la plus ancienne est déconnectée) ferme désormais en moins d’une seconde la connexion en direct des sessions concernées, au lieu de la laisser ouverte jusqu’au rechargement de la page. Une session déconnectée avec `meshloom-admin`, une session expirée ou un compte désactivé perd sa connexion en direct en moins de 30 secondes. Un écran qui garde seulement la connexion en direct ouverte, comme un affichage kiosque, reste désormais connecté : la connexion compte comme une visite. Les sessions expirées ou déconnectées sont supprimées de la base toutes les heures.
- **Un journal d'audit note les actions sensibles.** Meshloom écrit désormais une entrée pour chaque connexion (réussie ou non), déconnexion, session déconnectée, compte créé, renommé, désactivé ou réactivé, changement de mot de passe, création du premier administrateur, changement des réglages d'accès, robot créé, modifié ou supprimé, arrêt des robots, restauration de sauvegarde, import de clé privée, réinitialisation d'usine de la radio et mise à jour lancée, qu'elle vienne de l'interface web, de Home Assistant, de `meshloom-admin` ou de Meshloom lui-même (mise à jour automatique). Chaque entrée garde l'heure, le compte, l'adresse, la voie d'accès (réseau local, Home Assistant, ligne de commande) et l'issue ; jamais un mot de passe, un code, une session ni une clé. Les administrateurs le lisent avec `GET /api/auth/audit` (du plus récent au plus ancien, par pages) ; une visionneuse dans les Réglages viendra plus tard. Les entrées sont gardées 366 jours, puis supprimées toutes les heures ; les administrateurs changent cette durée avec `PATCH /api/auth/settings` (`audit_retention_days`, de 1 à 36500).
- **Le rôle de chaque compte est appliqué sur toutes les routes.** Le serveur vérifie désormais le rôle du compte derrière chaque requête : `admin` peut tout faire, `member` envoie et modifie contacts et canaux, `viewer` lit, et `kiosk` (un écran mural) ne lit que les canaux et ne reçoit jamais un message direct, ni par une route ni par la connexion en direct. Chaque route déclare la permission qu'elle exige, et une route qui n'en déclare pas est refusée à tous sauf à un administrateur. La documentation de l'API (`/docs`, `/openapi.json`) demande maintenant d'être connecté. Le premier administrateur et le compte importé de l'authentification Basic de la 4.x gardent tous les droits : une installation avec un seul administrateur ne voit aucun changement.
- **Les mauvais mots de passe ralentissent la connexion, sans jamais verrouiller un compte.** Après 5 mauvais mots de passe pour un compte depuis une même adresse, la tentative suivante attend 1 seconde, puis 2, 4 et ainsi de suite jusqu’à 1 minute ; la page de connexion indique combien de temps. Après 5 mauvais mots de passe sur un compte depuis n’importe où, il accepte au plus une tentative par seconde. Une adresse qui dépasse 100 mauvais mots de passe en 15 minutes est refusée un moment ; une adresse IPv6 compte pour son bloc /64. Un navigateur avec lequel vous vous êtes connecté est reconnu par un cookie dédié et entre toujours avec le bon mot de passe, quoi qu’envoie un attaquant ; il perd ce passe-droit après 5 mauvais mots de passe (oubliés après 15 minutes sans nouvelle erreur), et un changement de mot de passe, de rôle ou de nom du compte, sa désactivation, la restauration d’une copie de la base ou `meshloom-admin rotate-device-key` l’oublie jusqu’à la connexion suivante (un redémarrage le garde). Depuis un navigateur que Meshloom ne connaît pas, une installation Docker sans proxy de confiance n’a que la règle d’une tentative par seconde, qu’un attaquant rapide sur la même adresse peut accaparer ; derrière un reverse-proxy non listé dans les Proxies de confiance, tous les visiteurs comptent pour l’adresse du proxy. Les tentatives refusées ne coûtent aucune vérification de mot de passe, et chaque délai est noté une fois dans le journal d’audit.
- **Un code d’installation de `meshloom-admin setup-code` n’est plus remplacé dans votre dos.** Quand Meshloom n’avait pas pu préparer son premier démarrage et réessayait pendant que vous créiez l’administrateur, il émettait un nouveau code, et celui imprimé par la commande était refusé. Il le garde désormais.
- **La connexion est désormais obligatoire.** Toutes les routes de l'API et la connexion en direct demandent maintenant un compte ; seuls un contrôle de santé minimal (`/api/health` répond `{"ok": true}`), la connexion et la création initiale restent ouverts. Sur une nouvelle installation, vous créez d'abord le compte administrateur. Sur une instance qui avait déjà des données et pas de mot de passe, Meshloom demande aussi un **code d'installation** imprimé dans le journal du serveur à chaque démarrage, pour que personne d'autre sur votre réseau ne puisse se l'approprier après une mise à jour automatique. Si vous utilisiez `MESHCORE_BASIC_AUTH_USERNAME` / `MESHCORE_BASIC_AUTH_PASSWORD`, ils deviennent le compte administrateur au premier démarrage (même nom, même mot de passe), puis ne sont plus lus : retirez-les. Les scripts et capteurs Home Assistant qui envoient cet identifiant en Basic continuent de fonctionner jusqu'à la 5.2. Les mots de passe font au moins 12 caractères, diffèrent du nom d'utilisateur et ne peuvent pas être un mot de passe courant. Les sessions durent 30 jours après la dernière visite ; en HTTPS, le cookie est limité à cette adresse exacte. Les modifications (POST, PUT, PATCH, DELETE) venant d'un autre site web sont refusées, sauf si ce site figure dans les **origines autorisées** ; les scripts qui ne disent pas d'où ils viennent ne sont pas concernés. Les sondes qui lisaient le détail de `/api/health` sans se connecter n'obtiennent plus que `{"ok": true}`. Trop de vérifications de connexion en même temps reçoivent vite une réponse « réessayez dans un instant », et les connexions Basic erronées répétées depuis une même adresse sont refusées quelques secondes. L'add-on Home Assistant fait exception : voir plus bas.
- **Un site des Origines autorisées peut aussi ouvrir la connexion en direct.** Jusqu’ici, un tableau de bord ajouté dans **Réglages > Configuration locale > Origines autorisées** pouvait appeler l’API, mais sa connexion en direct (`/api/ws`) était refusée. L’API et la connexion en direct vérifient désormais la même liste, comparée de la même façon (la casse, un port par défaut comme `:443` et les noms de domaine internationaux n’y changent rien). Rien d’autre n’est accepté : une liste vide veut toujours dire même origine uniquement.
- **Les autres programmes du serveur ne peuvent plus lire la mémoire de Meshloom.** Sous Linux, le serveur se marque désormais comme non « dumpable » au démarrage : un autre programme lancé par le même utilisateur ne peut plus lire son environnement (l'identifiant d'accès Basic) ni sa mémoire (la clé privée de la radio). Effet de bord : pas de core dump du serveur, et des outils comme `py-spy` ou `gdb` demandent `sudo` pour s'y attacher.
- **Les autres sites ne peuvent plus ouvrir la connexion en direct.** Une page web d'un autre site, ouverte dans un navigateur qui atteint Meshloom, pouvait ouvrir la connexion en direct (`/api/ws`) et recevoir vos messages, vos paquets et l'état de la radio à leur arrivée, accès Basic activé ou non. Le serveur refuse maintenant cette connexion quand le navigateur indique qu'elle vient d'un autre site ; les scripts et les applications qui n'indiquent pas leur provenance ne sont pas concernés. Si Meshloom est derrière un proxy inverse qui remplace l'en-tête `Host`, faites-lui transmettre l'original dans `X-Forwarded-Host`, sinon rien n'arrive en direct (la barre latérale de Home Assistant le fait déjà).
- **Les autres sites web ne peuvent plus se servir de Meshloom via votre navigateur.** Le serveur répondait à n'importe quelle page web avec vos identifiants : un site ouvert dans un autre onglet pouvait lire vos messages, la clé privée de la radio et la sauvegarde complète. Il ne répond plus qu'à sa propre adresse (même origine). Meshloom lui-même, le serveur de dev Vite et l'ingress Home Assistant ne sont pas concernés. Si un tableau de bord ou un script servi depuis une autre adresse doit appeler l'API, ajoutez cette adresse dans **Réglages > Configuration locale > Origines autorisées** (vide par défaut ; pas de `*`).
- **Un test de notification qui échoue ne révèle plus votre réseau.** Les boutons **Test** d'un abonnement push et des destinations e-mail et webhook affichaient l'erreur réseau brute (connexion refusée, hôte inconnu, statut HTTP). Une page web pouvait s'en servir pour repérer les machines de votre réseau. Ils affichent maintenant un message traduit qui dit seulement quel type d'échec s'est produit. Le journal du serveur indique le type d'erreur et, pour le webhook, son hôte et son statut HTTP, mais plus l'adresse du webhook avec son jeton.
- **Les notifications ne joignent plus votre réseau privé sans votre accord.** Le webhook de notification et le Web Push refusent désormais une destination sur votre réseau local, sur cette machine ou sur une adresse de métadonnées cloud, vérifiée à l'enregistrement puis avant chaque envoi, et se connectent uniquement à l'adresse vérifiée (une réponse DNS qui change entre-temps ne peut pas les détourner ; les redirections ne sont pas suivies). Cochez **Autoriser une destination sur un réseau privé** pour un webhook vers Home Assistant, Node-RED ou un script sur votre réseau ; un webhook déjà configuré continue de fonctionner, et un avis unique vous le signale. **Accepter les points de terminaison push sur un réseau privé** (Réglages → Notifications) sert à un distributeur UnifiedPush auto-hébergé ; si un appareil déjà enregistré n’est pas un service push de navigateur, la mise à jour la coche pour vous afin qu’il continue de recevoir, et un avis le signale. Une destination refusée répond tout de suite : une page ne peut plus chronométrer un test pour savoir quelles machines existent, et un test push ne renvoie plus un statut différent en cas de délai dépassé. Les variables de proxy (`HTTPS_PROXY`) ne s'appliquent plus à ces deux sorties.
- **Les noms qui inversent le sens du texte ne peuvent plus réordonner ce qui les entoure.** Le nom de l'expéditeur dans un message, les noms de la liste des réactions, le titre de la conversation, ainsi que les infobulles et libellés pour lecteur d'écran qui citent un nom sont désormais isolés de leur contexte. Un message sans aucun expéditeur contenant une surcharge droite-à-gauche, un isolat, une espace de largeur nulle ou un autre caractère invisible du même genre est signalé comme paquet possiblement corrompu, comme avec un caractère de contrôle ; le texte légitime (séquences d'emoji, persan ou hindi, marques de direction arabes ou hébraïques) ne l'est pas. Les noms de la liste des conversations et du fil ne sont pas encore isolés.
- **Les sorties fanout ne joignent plus votre réseau privé sans votre accord, et Apprise n’accepte que des services examinés.** Les intégrations Webhook, Apprise, Amazon SQS (point d’accès personnalisé) et Envoi vers la carte (API personnalisée) refusent désormais une destination sur votre réseau local, sur cette machine ou sur une adresse de métadonnées cloud, vérifiée quand vous enregistrez une intégration activée puis avant chaque envoi ; cochez **Autoriser une destination sur un réseau privé (réseau local, localhost)** dans l’intégration pour Home Assistant, Gotify, ntfy ou un script sur votre réseau. Les intégrations déjà configurées continuent de fonctionner : la mise à jour coche la case pour elles et un avis unique les liste ; changer l’adresse de l’une d’elles décoche la case. Apprise n’accepte plus qu’une liste examinée de services (ntfy, Telegram, Discord, Gotify, Home Assistant, Matrix, e-mail, MQTT, Slack, Pushover, webhooks JSON/XML/formulaire et quelques autres, voir la page Fanout) et refuse les autres, comme les notifications de bureau, syslog et Amazon SNS/SES, quel que soit le réglage ; une adresse Telegram doit indiquer un identifiant de discussion ; Apprise n’envoie qu’à l’hôte saisi (pas de redirection, pas de découverte de serveur Matrix, pas de recherche de clé pour les e-mails), la restauration d’une ancienne sauvegarde ramène désactivée une intégration Apprise dont une adresse est refusée au lieu de la perdre, l’envoi vers la carte ne suit jamais de redirection, et le webhook fanout se connecte uniquement à l’adresse vérifiée. Les erreurs indiquent la ligne Apprise en cause et n’affichent plus l’URL.
- **Un appareil push dont l’adresse ne pourra jamais être acceptée est supprimé.** Avant chaque push, Meshloom vérifie de nouveau l’adresse de l’appareil et se connecte uniquement à l’adresse vérifiée, le certificat étant contrôlé pour le nom du service push. Un appareil dont l’adresse est mal formée, ou pointe désormais vers une adresse de métadonnées cloud ou de documentation (refusée quels que soient les réglages), est ignoré et supprimé, avec une ligne dans le journal (jamais son adresse complète, qui contient un jeton) ; quand seule la réponse DNS pointe là, il n’est supprimé que si un push au moins une heure plus tard la retrouve. Le journal de débogage ne montre plus non plus les adresses push : les lignes de requête de la bibliothèque HTTP, qui les contenaient, ne sont plus journalisées au niveau débogage. Un appareil refusé seulement parce que **Accepter les points de terminaison push sur un réseau privé** est décoché, ou dont le nom est introuvable pour l’instant, reste enregistré, et le journal le signale une fois.
- **Les notifications push restent ouvertes à votre réseau local seulement si un appareil s’y trouve vraiment.** La mise à jour n’active plus l’autorisation du réseau privé pour un distributeur UnifiedPush public comme ntfy.sh : seuls les appareils dont l’adresse est une IP privée, un nom sans point ou en `.local`/`.lan`/`.home.arpa`/`.internal`/`.localdomain` la reçoivent (aucune résolution DNS), et celle posée à tort par la version précédente est retirée tant que l’avis n’a pas été fermé, avec un avis et un bouton pour la réautoriser en un clic quand un appareil se cache derrière un nom qui peut pointer vers votre réseau (DNS interne, Tailscale). Un push refusé par cette vérification n’est plus compté comme un échec : l’appareil n’est donc plus supprimé après des refus répétés. Restaurer une sauvegarde d’avant la protection réintroduit de la même façon l’autorisation pour les appareils du réseau local, comme pour le webhook.
- **Le partage de liens et de fichiers signera avec sa propre clé, jamais celle de la radio.** Meshloom peut désormais créer une clé d’instance pour le futur service de partage : une clé aléatoire gardée dans sa base, sans lien avec l’identité de votre radio dans l’annuaire Community, si bien que ce que vous partagez ne peut pas être rattaché à votre nœud et qu’une nouvelle radio garde la même identité. Elle est créée la première fois que le partage en a besoin, n’apparaît jamais dans l’API des réglages, une sauvegarde ou les journaux, et une restauration en donne une nouvelle à l’instance. Les jetons Community ne changent pas.
- **Les secrets des intégrations ne se relisent plus.** L’API, l’état de santé et l’instantané de débogage ne renvoient plus les URL Apprise, le secret de signature du webhook, les mots de passe MQTT ni la clé secrète et le jeton de session Amazon SQS de vos intégrations : un secret enregistré s’affiche `********`, et la liste Apprise ne montre que le service et l’hôte de chaque adresse. Dans l’éditeur, laissez un champ secret vide pour le conserver, saisissez-en un nouveau pour le remplacer, ou appuyez sur **Effacer**. L’erreur d’une intégration ne cite plus son URL Apprise. Le téléchargement de la sauvegarde JSON (`GET /api/settings/backup/json`) ne contient plus le mot de passe e-mail (SMTP) ni le secret du webhook de notification : sa restauration conserve ceux déjà enregistrés. La sauvegarde complète les contient toujours. Une intégration restaurée qui avait le droit de joindre votre réseau privé revient désactivée, avec un avis, pour que vous la vérifiiez avant de la réactiver.
- **Les URL et en-têtes des webhooks et l’identifiant de clé d’accès SQS ne se relisent plus non plus.** Une URL de webhook contient souvent son jeton (Discord, Slack, Home Assistant), tout comme des en-têtes personnalisés tels que `Authorization`. L’API et l’éditeur ne les affichent plus : la liste des intégrations et le webhook de notification ne montrent que le schéma et l’hôte (`https://hooks.example.org/****`), un en-tête garde son nom et sa valeur s’affiche `********`, et l’identifiant de clé d’accès Amazon SQS s’affiche `********` comme la clé secrète. Laissez un champ tel quel pour conserver la valeur enregistrée, ou saisissez-en une nouvelle. Changer l’URL d’un webhook oblige à saisir de nouveau les valeurs de ses en-têtes et son secret de signature, et une restauration qui envoie un webhook ailleurs les supprime avec un avertissement. L’erreur d’une intégration ne cite plus le chemin ni la requête de son URL de webhook, ni la valeur d’un en-tête. Le téléchargement de la sauvegarde JSON (`GET /api/settings/backup/json`) ne contient plus l’URL du webhook de notification ; sa restauration conserve celle déjà enregistrée, et la sauvegarde complète la contient toujours. Les intégrations, les réglages de notification et les sauvegardes de la 4.18.x existants continuent de fonctionner sans rien ressaisir.
- **Meshloom note les noms sous lesquels on l'atteint (contrôle du Host, mode observation).** Meshloom compare désormais le nom saisi dans le navigateur aux noms qu'il connaît : une adresse IP, `localhost`, les noms `.local`, `.home.arpa`, `.lan` et `.internal`, le nom de cette machine, une adresse publique déclarée et le nom de Meshloom Remote (l'ingress de Home Assistant et le relais ne sont jamais contrôlés). En 5.0.0 tout autre nom, comme le nom DNS de votre reverse-proxy, fonctionne encore : il est seulement noté une fois dans le journal et gardé dans une liste que renvoie `GET /api/auth/settings` (`rejected_hosts`, `host_guard_mode`), pour que la mise à jour ne casse rien. À partir de la 5.1, un nom inconnu sera refusé avec une erreur qui le nomme (`421 auth_host_rejected`). Un réseau de confiance ne connecte jamais personne sous un nom inconnu, dans les deux modes.
- **Adresses publiques et mode du contrôle du Host dans les réglages.** **Réglages → Utilisateurs et accès → Adresses publiques** (administrateurs) liste les adresses sous lesquelles on atteint Meshloom (`https://nom[:port][/préfixe]`), les noms inconnus vus récemment par le contrôle du Host avec un bouton **Autoriser** qui en déclare un, et un interrupteur du mode observation vers le mode strict (un nom inconnu reçoit alors une erreur 421 qui le nomme). Meshloom refuse d’activer le mode strict, ou de retirer une adresse, si cela rejetterait l’adresse que vous utilisez en ce moment ; il n’existe aucune variable d’environnement. De plus : seuls les noms simples (lettres, chiffres, points, tirets) sont enregistrés et journalisés, la ligne de journal d’un nouveau nom inconnu est limitée en fréquence, et la réponse 421 porte désormais les en-têtes de sécurité. Le mode strict liste toujours les noms refusés, **Autoriser** demande de confirmer le nom exact (il est fourni par l’auteur de la requête et peut venir d’un attaquant), les noms internationaux sont convertis en forme `xn--`, et `meshloom-admin host-guard-observe` revient à l’observation depuis la machine.

---

## [4.18.2] - 2026-10-10

Re-running the installer on a Docker stack now keeps the changes you made to your compose file.

### Fixed

- **The installer keeps your edits to docker-compose.yml.** Upgrading from 4.17 means re-running the installer once. If you had edited your compose file, for instance to add a port, it stopped and asked you to edit the file by hand or to regenerate it, which lost your changes. It now lists your changes and offers to keep them, which is the default. It then updates only its own lines (the image and the update helper lines) and leaves yours alone. It checks the result with Docker Compose before writing anything and keeps a `docker-compose.yml.bak-<date>` copy. If it cannot adapt your file, it does not touch it and asks whether to regenerate it.
- **A stack in /root/meshloom is no longer replaced by an empty one.** Run as a regular user, the installer could not see a stack kept under `/root` and offered to create a new one in your home folder, which started on an empty database. It now stops and asks you to run it again as root.

---

### Français

Relancer l'installeur sur une installation Docker conserve désormais les modifications apportées à votre fichier compose.

#### Corrections

- **L'installeur garde vos modifications de docker-compose.yml.** Passer de la 4.17 à la 4.18 demande de relancer l'installeur une fois. Si vous aviez modifié votre fichier compose, par exemple pour ajouter un port, il s'arrêtait et vous demandait de modifier le fichier à la main ou de le régénérer, ce qui effaçait vos changements. Il liste maintenant vos modifications et propose de les garder, ce qui est le choix par défaut. Il ne met alors à jour que ses propres lignes (l'image et les lignes de l'assistant de mise à jour) et laisse les vôtres. Il vérifie le résultat avec Docker Compose avant d'écrire quoi que ce soit et garde une copie `docker-compose.yml.bak-<date>`. S'il ne sait pas adapter votre fichier, il n'y touche pas et vous demande s'il faut le régénérer.
- **Une installation dans /root/meshloom n'est plus remplacée par une installation vide.** Lancé en simple utilisateur, l'installeur ne voyait pas une installation rangée sous `/root` et proposait d'en créer une nouvelle dans votre dossier personnel, qui démarrait sur une base vide. Il s'arrête maintenant et vous demande de le relancer en root.

---


## [4.18.1] - 2026-10-10

Two fixes: the mesh visualizer no longer empties itself when the server clock is off, and the Home Assistant add-on radio proxy answers on its published port.

### Fixed

- **The mesh visualizer keeps its nodes when the server clock is off.** The graph drew each packet, then erased it within a second, leaving only your own node and "Listening for mesh traffic…". It compared the server's packet time with your browser's clock, so a server more than 5 minutes behind made every packet look stale. Live traffic is now timed by when your browser receives it. The same fix applies to the time windows of the raw packet statistics and to the live view.
- **Home Assistant add-on: the radio proxy is reachable.** The add-on publishes port 5051, but the proxy kept listening on 5001, so apps could not connect. It now listens on the published port.
- **Community MQTT settings are checked.** The WebSocket path must start with `/` and contain no spaces, and the email, if set, must look like an address.

---

### Français

Deux corrections : le visualiseur de mesh ne se vide plus quand l'horloge du serveur est décalée, et le proxy radio de l'add-on Home Assistant répond sur son port publié.

#### Corrections

- **Le visualiseur de mesh garde ses nœuds quand l'horloge du serveur est décalée.** Le graphe dessinait chaque paquet puis l'effaçait en moins d'une seconde, ne laissant que votre propre nœud et « En écoute du trafic mesh… ». Il comparait l'heure du paquet donnée par le serveur à l'horloge de votre navigateur : un serveur en retard de plus de 5 minutes rendait chaque paquet trop ancien. Le trafic en direct est maintenant daté à sa réception par votre navigateur. La même correction s'applique aux fenêtres de temps des statistiques de paquets bruts et à la vue en direct.
- **Add-on Home Assistant : le proxy radio est joignable.** L'add-on publie le port 5051, mais le proxy restait à l'écoute sur 5001 : les applications ne pouvaient pas s'y connecter. Il écoute maintenant sur le port publié.
- **Les réglages MQTT Community sont vérifiés.** Le chemin WebSocket doit commencer par `/` et ne contenir aucun espace, et l'e-mail, s'il est renseigné, doit ressembler à une adresse.

---


## [4.18.0] - 2026-10-10

Updates are now signed from end to end, and Meshloom opens faster and stays fast on busy meshes.

### Before you upgrade

- **Fedora: upgrade once by hand.** The 4.17 updater cannot move a Fedora install forward: it asks dnf to install a package that is already installed, which does nothing and still reports success. Run `sudo dnf upgrade --refresh meshloom` once. From 4.18 on, the in-app update works.
- **Docker installs made with the one-line installer: run it again once.** This switches your stack to the new, secured update helper. Your radio settings are kept, your compose file is backed up first, and the installer stops and shows you the difference if you edited that file. Until you do, Settings, Updates shows a banner reminding you.
- **The apt and dnf repository is now signed.** This is its first signed release. The key fingerprint is `D852 F2F0 892A BF37 9F52 D110 FB3E B7BB C439 35C8` (Meshloom Release Signing <releases@meshloom.app>). Package installs switch to it on their own during this upgrade.

### Security

- **Updates install only what Meshloom signed.** The part of the updater that runs as root no longer reads anything the app can write. It takes its target from the signed repository or signed release files, refuses unsigned or "trusted" sources, refuses downgrades, and stops rather than guess when something is off. Docker installs are pinned to an exact, verified image.
- **Signed release files.** Each release publishes a signed `SHA256SUMS` for its packages, release zip and installer, and a signed `OCI-DIGESTS` for the Docker image. Debian and RPM packages are signed too, and builds carry provenance attestations, so you can check where a file came from.
- **Map security fix.** The live map library is upgraded to MapLibre 6, which fixes a critical advisory (GHSA-jrc7-96c5-q579).

### Faster

- **The first load is about a third lighter.** Rarely used panels load when you open them, and only your language is downloaded.
- **Busy meshes no longer slow everything down.** A repeated advert is about 15 times cheaper to handle, unread counts about 19 times faster to compute, and channel messages are matched to their channel more directly. Since these all ran under the same database lock, every other request waits less too.
- **A slow browser tab no longer holds up the others.** Each open tab gets its own bounded queue. During a burst of radio traffic, raw packet frames are dropped for a tab that falls behind instead of disconnecting it.

### Fixed

- **Reconnecting is calmer and loses nothing.** After a dropped connection, the app retries with a growing delay instead of a fixed one, and a late snapshot can no longer overwrite newer messages that arrived live.
- **Numbers follow your language.** Counts and distances use your language's separators, for example `0,3` in French.
- **Shutdown and database upgrades are safer.** Each shutdown step is bounded and logged, the database is always closed, and each database upgrade is applied together with its version number, so an interrupted upgrade cannot leave it half done.

### What's new

- **Optional raw packet retention.** A new setting, `raw_packet_retention_days` (off by default), deletes packets that were never decrypted after that many days and gives the space back. Packets deleted this way cannot be decrypted later, even if you add the right channel or contact key. For now it is set through the API only.

---

### Français

Les mises à jour sont désormais signées de bout en bout, et Meshloom s'ouvre plus vite et reste rapide sur un réseau chargé.

#### Avant de mettre à jour

- **Fedora : mettez à jour une fois à la main.** Le système de mise à jour de la 4.17 ne fait pas avancer une installation Fedora : il demande à dnf d'installer un paquet déjà installé, ce qui ne fait rien et annonce pourtant un succès. Lancez une fois `sudo dnf upgrade --refresh meshloom`. À partir de la 4.18, la mise à jour depuis l'application fonctionne.
- **Installations Docker faites avec l'installeur en une ligne : relancez-le une fois.** Il fait passer votre installation au nouveau système de mise à jour sécurisé. Vos réglages radio sont conservés, votre fichier compose est sauvegardé d'abord, et l'installeur s'arrête en montrant la différence si vous l'avez modifié. D'ici là, Réglages, Mises à jour affiche un bandeau de rappel.
- **Le dépôt apt et dnf est maintenant signé.** C'est sa première version signée. L'empreinte de la clé est `D852 F2F0 892A BF37 9F52 D110 FB3E B7BB C439 35C8` (Meshloom Release Signing <releases@meshloom.app>). Les installations par paquet passent dessus d'elles-mêmes pendant cette mise à jour.

#### Sécurité

- **Les mises à jour n'installent que ce que Meshloom a signé.** La partie du système de mise à jour qui tourne en root ne lit plus rien que l'application peut écrire. Elle prend sa cible dans le dépôt signé ou les fichiers de version signés, refuse les sources non signées ou « de confiance », refuse les retours en arrière, et s'arrête plutôt que de deviner quand quelque chose cloche. Les installations Docker sont épinglées sur une image exacte et vérifiée.
- **Fichiers de version signés.** Chaque version publie un `SHA256SUMS` signé pour ses paquets, son archive et son installeur, et un `OCI-DIGESTS` signé pour l'image Docker. Les paquets Debian et RPM sont signés eux aussi, et les builds portent des attestations de provenance : on peut vérifier d'où vient un fichier.
- **Correctif de sécurité de la carte.** La bibliothèque de la carte en direct passe à MapLibre 6, qui corrige une alerte critique (GHSA-jrc7-96c5-q579).

#### Plus rapide

- **Le premier chargement est allégé d'environ un tiers.** Les panneaux peu utilisés se chargent à l'ouverture, et seule votre langue est téléchargée.
- **Un réseau chargé ne ralentit plus tout.** Une annonce répétée coûte environ 15 fois moins cher à traiter, les compteurs de non-lus se calculent environ 19 fois plus vite, et les messages de canal retrouvent leur canal plus directement. Comme tout cela passait par le même verrou de base de données, les autres requêtes attendent moins aussi.
- **Un onglet lent ne bloque plus les autres.** Chaque onglet ouvert a sa propre file bornée. Pendant une rafale de trafic radio, les paquets bruts sont abandonnés pour l'onglet qui prend du retard au lieu de le déconnecter.

#### Corrections

- **La reconnexion est plus sereine et ne perd rien.** Après une coupure, l'application réessaie avec un délai croissant plutôt que fixe, et un instantané en retard ne peut plus écraser des messages plus récents arrivés en direct.
- **Les nombres suivent votre langue.** Compteurs et distances utilisent les séparateurs de votre langue, par exemple `0,3` en français.
- **Arrêt et mises à niveau de la base plus sûrs.** Chaque étape de l'arrêt est bornée et journalisée, la base est toujours fermée, et chaque mise à niveau de la base est appliquée avec son numéro de version : une mise à niveau interrompue ne peut plus la laisser à moitié faite.

#### Quoi de neuf

- **Conservation des paquets bruts, en option.** Un nouveau réglage, `raw_packet_retention_days` (désactivé par défaut), supprime après ce nombre de jours les paquets jamais déchiffrés et récupère la place. Un paquet supprimé ainsi ne pourra plus être déchiffré, même si vous ajoutez plus tard la bonne clé de canal ou de contact. Pour l'instant, il se règle uniquement par l'API.

---


## [4.17.0] - 2026-10-07

The Home Assistant map is back, and "Mark all read" is now on the conversation list, one tap away.

### Fixed

- **The map shows again on Home Assistant.** The tiles had been replaced by an "access blocked" picture from OpenStreetMap. You get the real map back.

### What's new

- **"Mark all read" on the conversation list.** When some conversations are unread, a "Mark all read" control appears. One tap clears every unread badge. It used to sit in Tools, out of the way. It is now where you need it.

---

### Français

La carte revient sur Home Assistant, et « Tout lu » est maintenant sur la liste des conversations, à portée de pouce.

#### Corrections

- **La carte s'affiche de nouveau sur Home Assistant.** Les tuiles étaient remplacées par une image « accès bloqué » d'OpenStreetMap. La vraie carte est de retour.

#### Quoi de neuf

- **« Tout lu » sur la liste des conversations.** Dès qu'il reste des conversations non lues, un bouton « Tout lu » apparaît. Un geste efface tous les badges d'un coup. Il se cachait dans Outils ; il est maintenant là où on en a besoin.

---

## [4.16.1] - 2026-09-25

Naming your region no longer depends on a repeater answering.

### Fixed

- **Region discovery says what went wrong.** Every failure looked the same: a repeater out of range, a radio whose contact list is full and an unexpected error all reported "did not answer", which left a count and no next step. Each repeater now says which of those happened, and a full contact list, which fails every repeater at once for a reason none of them caused, is named as such.
- **Discovery gives a repeater a fair chance.** A single request was sent, though a lost packet over the air is ordinary, and every repeater got the same ten seconds whether it was next door or four hops away. A second request now goes out when nothing comes back, and the wait follows the distance, with a ceiling so one unreachable node cannot hold the sweep open.

### What's new

- **Propose regions from traffic you have already heard.** Asking repeaters is not the only way to name a region, and it is the one that depends on the radio reaching them. A packet cannot be asked which region it belongs to, but a proposed name can be checked against packets already stored, which needs no radio and answers in a second. Settings, Radio now offers this next to the discovery sweep: it proposes candidates from the scopes you are configured with and the places your repeaters are named after, says how much of your traffic each one explains, and adds the right one to the list in a click.

---

### Français

Nommer sa région ne dépend plus de la réponse d'un répéteur.

#### Corrections

- **La découverte des régions dit ce qui a échoué.** Toutes les erreurs se ressemblaient : un répéteur hors de portée, une radio dont le carnet de contacts est plein et une erreur inattendue affichaient toutes « n'a pas répondu », ce qui laissait un compteur et aucune marche à suivre. Chaque répéteur indique désormais laquelle s'est produite, et le carnet plein, qui fait échouer tous les répéteurs d'un coup pour une raison qu'aucun d'eux n'a causée, est nommé comme tel.
- **La découverte laisse sa chance à un répéteur.** Une seule requête partait, alors qu'un paquet perdu sur les ondes est banal, et chaque répéteur recevait les mêmes dix secondes qu'il soit voisin ou à quatre sauts. Une seconde requête part maintenant quand rien ne revient, et l'attente suit la distance, avec un plafond pour qu'un nœud injoignable ne retienne pas le balayage.

#### Quoi de neuf

- **Proposer des régions depuis le trafic déjà reçu.** Interroger les répéteurs n'est pas la seule façon de nommer une région, et c'est celle qui dépend de la radio pour les atteindre. On ne peut pas demander à un paquet de quelle région il vient, mais on peut confronter un nom proposé aux paquets déjà stockés, ce qui n'utilise pas la radio et répond en une seconde. Réglages, Radio propose désormais cela à côté du balayage : des candidats tirés de vos portées configurées et des lieux d'après lesquels vos répéteurs sont nommés, le nombre de paquets que chacun explique, et un clic pour ajouter le bon à la liste.

---

## [4.16.0] - 2026-09-23

Meshloom can run a radio test through Community and read the result on a map. Locate can show a coverage disk when Community knows a first repeater.

### What's new

- **A radio test with a map.** Tools → Test radio sends one flood, then shows who heard it and how the signal travelled. Meshloom ears wear the logo; other stations are coloured by role, as on the live map, and the margin lists them.
- **Coverage on Locate.** When Community knows a first repeater, Locate adds a coverage disk.

---

### Français

Meshloom peut lancer un test radio via Community et lire le résultat sur une carte. La localisation affiche aussi un disque de couverture quand Community connaît un premier répéteur.

#### Quoi de neuf

- **Un test radio avec carte.** Outils → Test radio : un flood, puis une carte de qui l’a entendu et du chemin du signal. Les oreilles Meshloom portent le logo ; les autres sont colorés selon leur rôle, comme sur Live. On les retrouve dans la marge.
- **Couverture à la localisation.** Quand Community connaît un premier répéteur, Localiser ajoute un disque de couverture.

---

## [4.15.0] - 2026-09-21

Meshloom shows more clearly what the radio is hearing. The traffic screen is easier to follow, the network has a notebook for its backstage, and after you send a message you can see how many stations heard it. The radio battery is back on screen.

### What's new

- **The traffic screen finally reads in plain words.** Tools → Packet feed: each line has a name you understand, not Unknown everywhere. You can search, filter, and pause the list. Tap a line to open the person, the map, or the network picture. You can look back at older traffic while live traffic keeps arriving.
- **A notebook for what the network does behind the scenes.** Tools → Control journal: logins, status checks, the small exchanges the network makes outside chat. It is laid out like conversations, not a dump of lines. Other people's private details stay hidden—you only see that something happened.
- **The "heard by" ear on what you send, too.** A small ear shows how many Community stations say they heard the message. It was already on incoming channel messages. It now appears on private messages and on what you send, and the number goes up as more stations hear it.
- **The radio battery visible again.** After a redesign, the charge had vanished from the screen. It is back next to the radio status; on a computer there is enough room to read it.

### Fixed

- **Docker install with the radio plugged in.** If the radio is on USB or Bluetooth, the installer finds it by itself. Several devices: it asks which one. A radio already on the network needs nothing extra.

---

### Français

Meshloom montre mieux ce que la radio entend. On s’y retrouve dans le trafic, on lit un carnet des coulisses du réseau, et après un message on voit combien de postes l’ont entendu. La batterie de la radio est revenue à l’écran.

#### Quoi de neuf

- **L’écran du trafic se lit enfin.** Outils → Flux de paquets : chaque ligne porte un nom qu’on comprend, au lieu de « Inconnu » partout. On peut chercher, filtrer, mettre la liste en pause. Un clic ouvre la personne, la carte ou le schéma du réseau. On peut remonter l’ancien trafic pendant que le direct continue d’arriver.
- **Un carnet pour les coulisses du réseau.** Outils → Journal de contrôle : connexions, demandes d’état, les petits échanges que le réseau se fait hors des discussions. C’est rangé comme des conversations, pas comme une pluie de lignes. Ce qui est privé chez les autres reste invisible ; on voit seulement qu’il s’est passé quelque chose.
- **L’oreille « qui a entendu », aussi sur ce qu’on envoie.** Une petite oreille indique combien de postes Community disent avoir entendu le message. Elle était déjà là sur les messages de salon reçus. Elle apparaît maintenant sur les messages privés et sur ceux qu’on envoie, et le chiffre monte quand d’autres l’entendent.
- **La batterie de la radio, de nouveau visible.** Après une refonte, le pourcentage avait disparu de l’écran. Il est revenu à côté de l’état de la radio ; sur ordinateur, il a assez de place pour se lire.

#### Corrections

- **Installer Docker avec la radio branchée.** Si la radio est en USB ou en Bluetooth, l’installateur la trouve tout seul. Plusieurs appareils : il demande lequel. Radio déjà sur le réseau : rien de plus à faire.

---

## [4.14.2] - 2026-09-20

A thumbs-up in the conversation list used to show up as coded text. A conversation you watched would slip down as others spoke. And the locate map could empty itself while Community was still thinking.

### What's new

- **The list now says who reacted.** In the chat itself the emoji was already on the message. The conversation list, though, showed `r:90fd:00`. It now reads as a sentence: Alice reacted 👍 to "see you at 8". A GIF or a place says so in plain words, too.
- **Pin keeps a chat at the top of the list.** Favourites are unchanged: still the shortcut row of faces at the top. Pinning is different—it holds a contact or channel up in the main list, still sorted among pins by who spoke last. Use the chevron on each row, or the same menu from the chat header: pin, notifications, mute, favourite, a channel's regional setting, leave or delete.

### Fixed

- **Locate someone from what you already know.** Start typing a name and matching contacts from your list show up right away. The map is drawn from your radio and local store first. If Community later adds people it has heard, those circles appear when they arrive. When Community is slow or silent, the local map stays put instead of going blank.

---

### Français

Un pouce en l'air, dans la liste, s'écrivait en code. Une conversation qu'on suivait glissait vers le bas quand d'autres parlaient. Et la carte de localisation pouvait se vider pendant que Community réfléchissait encore.

#### Quoi de neuf

- **Dans la liste, une réaction se lit comme une phrase.** Dans le fil, l'emoji était déjà sur le message. C'est la liste qui montrait `r:90fd:00`. Maintenant : Alice a réagi par 👍 à « on se voit à 20 h ». Un GIF ou un lieu s'écrivent aussi en clair.
- **Épingler garde un fil en haut de la liste.** Les favoris, eux, ne changent pas : toujours la rangée de raccourcis en haut. L'épingle, c'est autre chose — elle maintient un contact ou un salon en tête dans la liste principale, toujours triés entre épinglés selon le dernier message. Le chevron sur chaque ligne, ou le même menu depuis l'en-tête du fil : épingler, notifications, sourdine, favori, réglage régional du salon, quitter ou supprimer.

#### Corrections

- **Localiser quelqu'un avec ce qu'on connaît déjà.** On commence à taper un nom et les contacts correspondants de la liste apparaissent tout de suite. La carte se trace d'abord avec la radio et ce que Meshloom a en local. Si Community ajoute ensuite des personnes qu'il a entendues, les cercles s'affichent quand ils arrivent. Si Community est lent ou ne répond pas, la carte locale reste à l'écran au lieu de disparaître.

---

## [4.14.1] - 2026-09-19

The conversation header had two bells. One now picks how you are notified. The other is a speaker, and it mutes a channel for a while.

### Fixed

- **The bell is for notifications.** Open it and tick Push, Email and/or Webhook for that conversation. An old "bell on" used to turn every medium on, so people who only wanted a phone nudge got every message by email.
- **The slashed speaker is for mute.** Fifteen minutes, half an hour, one hour, a few hours, a day, or until you unmute.

---

### Français

L'en-tête d'une conversation avait deux cloches. L'une choisit maintenant comment on vous prévient. L'autre est un haut-parleur : elle met le salon en sourdine pour un temps.

#### Corrections

- **La cloche, c'est les notifications.** On l'ouvre, on coche Push, e-mail et/ou webhook pour cette conversation. Une ancienne cloche activée allumait tout, donc ceux qui voulaient juste une alerte sur le téléphone recevaient chaque message par e-mail.
- **Le haut-parleur barré, c'est la sourdine.** Quinze minutes, une demi-heure, une heure, quelques heures, une journée, ou jusqu'à ce qu'on le réactive.

---

## [4.14.0] - 2026-09-19

Meshloom can now call you when something is off. This release watches the nodes you already poll, delivers the news the way you want, and makes managing several Meshloom instances simpler.

### What's new

- **Telemetry alerts for the nodes you already watch.** A low battery or a silent repeater used to be something you noticed in a dashboard, if you were looking. Settings → Alerts now watches the nodes you already poll: battery, noise, a missing GPS fix, missed polls, and the sensor readings Meshloom already collects. Defaults apply everywhere; a single node can have its own thresholds. Alerts stay quiet until you track a node and turn a rule on.
- **Choose how each event reaches you.** Push was the only voice. Email and a webhook are now destinations you set once in Settings → Notifications.
- **Name the server, for people running more than one.** Someone with Meshloom at home, at work and at a relative's house had three interfaces that looked identical. A band across the top can now say which one you opened. It lives with the instance, so every phone and laptop sees the same name. Size, weight, a handful of fonts, and a colour you can tell apart at a glance. Empty means no band at all — still the default.
- **Favourites wear a star.** A long mixed list used to look the same for everyone. A small star on the avatar now marks the people and rooms you pinned.
- **Discovered hashtag rooms wait in their own inbox.** Channels Meshloom discovers from Meshloom Community no longer land in Chat as if you had joined them. They wait under Tools → Discovered channels until you adopt or refuse them, and they stay out of the usual notification noise. Rooms you already have stay where they are.

---

### Français

Meshloom peut désormais vous appeler quand quelque chose cloche. Cette version veille sur les nœuds que vous interrogez déjà, vous prévient comme vous le souhaitez, et rend plus simple la gestion de plusieurs instances de Meshloom.

#### Quoi de neuf

- **Des alertes de télémétrie pour les nœuds que vous suivez déjà.** Une batterie faible ou un répéteur silencieux, c'était quelque chose qu'on voyait sur un tableau de bord, si on regardait. Réglages → Alertes surveille désormais les nœuds que vous interrogez déjà : batterie, bruit, un GPS qui disparaît, des sondes manquées, et les capteurs que Meshloom collecte déjà. Les seuils par défaut s'appliquent partout ; un nœud peut avoir ses propres seuils. Les alertes restent silencieuses tant que vous ne suivez pas un nœud et n'activez pas une règle.
- **Choisissez comment chaque événement vous atteint.** La notification push était la seule voix. L'e-mail et un webhook sont désormais des destinations que vous réglez une fois dans Réglages → Notifications.
- **Nommez le serveur, pour ceux qui en font tourner plusieurs.** Quelqu'un avec Meshloom à la maison, au travail et chez un proche avait trois interfaces identiques. Un bandeau en haut de page peut désormais dire laquelle vous avez ouverte. Il vit avec l'instance, donc chaque téléphone et chaque ordinateur voient le même nom. Taille, graisse, quelques polices, et une couleur qu'on distingue d'un coup d'œil. Vide, c'est aucun bandeau — toujours le défaut.
- **Les favoris portent une étoile.** Une longue liste mélangée avait le même visage pour tout le monde. Une petite étoile sur l'avatar marque désormais les personnes et les salons que vous avez épinglés.
- **Les salons hashtag découverts attendent dans leur propre boîte.** Les canaux que Meshloom découvrent depuis Meshloom Community n'atterrissent plus dans Discussions comme si vous les aviez rejoints. Ils attendent sous Outils → Canaux découverts jusqu'à ce que vous les adoptiez ou les refusiez, et ils restent hors du bruit habituel des notifications. Les salons que vous avez déjà restent où ils sont.

---

## [4.13.6] - 2026-09-19

The mesh graph still labelled unknown hops as hex, even when Meshloom Community already knew the name.

### Changed

- **Name unknown graph nodes from Community.** Repeaters and other hops the radio does not know can pick up a Community directory name. An orange globe marks that it came from the directory, not your contact list.
- **Keep those lookups alive on a busy mesh.** Incoming packets no longer cancel the request, so names can still appear while traffic is flowing.

1-byte hops stay hex. A local contact or advert-path hint still wins.

---

### Français

Le graphe du mesh affichait encore les hops inconnus en hex, même quand Meshloom Community connaissait déjà le nom.

#### Modifications

- **Nommer les nœuds inconnus du graphe via Community.** Les répéteurs et autres hops que la radio ne connaît pas peuvent recevoir un nom de l’annuaire Community. Un globe orange indique que ça vient de l’annuaire, pas de votre liste de contacts.
- **Garder ces lookups vivants sur un mesh actif.** Les paquets entrants n’annulent plus la requête, donc les noms peuvent encore apparaître pendant que le trafic circule.

Les hops 1 octet restent en hex. Un contact local ou un hint de chemin d’advert gagne toujours.

---

## [4.13.5] - 2026-09-19

Install still painted a red `systemctl` error after a successful package upgrade, because Meshloom waited inside the service that the upgrade restarts.

### Changed

- **Click Install without the leftover systemctl error.** The app no longer blocks on `systemctl start`. It drops a request file; systemd starts the helper. The old process is killed so the tab sees a restart and reloads on the new version, even if the last job file still says failed.
- **The next Install click starts.** After this upgrade the update watcher is already running, so a later apply is not a silent 202.

Auto-update windows and non-package installs are unchanged.

---

### Français

Installer affichait encore une erreur `systemctl` rouge après une mise à jour de paquet réussie : Meshloom attendait à l’intérieur du service que l’upgrade redémarre.

#### Modifications

- **Cliquer sur Installer sans l’erreur systemctl résiduelle.** L’app ne bloque plus sur `systemctl start`. Elle pose un fichier de demande ; systemd lance le helper. L’ancien process est tué pour que l’onglet voie un redémarrage et se recharge sur la nouvelle version, même si le dernier job dit encore failed.
- **Le clic Installer suivant part.** Après cette mise à jour, le watcher est déjà actif, donc un apply plus tard n’est plus un 202 silencieux.

Les fenêtres d’auto-update et les installs hors paquet ne changent pas.

---

## [4.13.4] - 2026-09-18

`#live` was still painting Community nodes that had not advertised for days, because Stats treated every directory pull as a fresh sighting.

### Changed

- **Hide Community pins after 24 hours.** `#live` only keeps a directory pin if the node was last heard within 24 hours. Re-importing the catalogue no longer refreshes that clock.
- **Show a pin when a live packet has GPS.** An origin or hop with exact coordinates is drawn as soon as the frame arrives, without waiting for the next directory fetch.

`#map` and local GPS contacts are unchanged.

---

### Français

`#live` affichait encore des nœuds Community qui n’avaient plus annoncé depuis plusieurs jours : Stats prenait chaque rechargement de l’annuaire pour une nouvelle observation.

#### Modifications

- **Masquer les épingles Community après 24 heures.** Sur `#live`, une épingle d’annuaire ne reste que si le nœud a été entendu dans les 24 dernières heures. Relire le catalogue ne remet plus cette horloge à zéro.
- **Afficher une épingle dès qu’un paquet live a un GPS.** Une origine ou un saut avec des coordonnées exactes est dessiné à l’arrivée de la trame, sans attendre le prochain chargement de l’annuaire.

`#map` et les contacts GPS locaux ne changent pas.

---

## [4.13.3] - 2026-09-18

Give your mesh a voice. This release turns Live View into a living, breathing sensory experience with procedural sound effects, upgrades your mesh routing clarity with 2-byte path hashing by default, and proudly badges fellow Meshloom operators across the Community map.

### What's new

- **Hear the mesh pulse in real time.** Live View now features procedural audio synthesis for packets travelling across the air. Listen to your network with three distinct sound themes: gentle **Bubbles** for subtle monitoring, futuristic **Laser** pulses that shift pitch with travel time, or retro **8-bit** arcade bleeps. It is entirely synthesized in your browser (no extra assets to download), spatial and viewport-aware so off-screen traffic won't overwhelm you, polyphony-limited to 4 voices, and starts politely muted on every reload so it never plays uninvited.
- **2-byte path hashing by default for cleaner routes.** On busy meshes, 1-byte hop hashes collide constantly, muddying routes and traceroutes. Meshloom now automatically opts supported radios into 2-byte path hashing (up to 32 hops across 65,536 hash buckets), giving you dramatically sharper route visibility. If you intentionally prefer 1-byte, Meshloom remembers your choice across reconnects. A discreet banner in the app lets you know if your radio is still running 1-byte mode, with a one-click shortcut to settings.
- **Spot fellow Meshloom nodes on the Community map.** When viewing packet reach and observers who heard your transmission, nodes powered by Meshloom now proudly display the Meshloom mark next to their name. See your local Meshloom community grow at a glance.
- **Collapsible Live View legend.** Clean up your map viewport whenever you want: the entire map legend (packet types and roles) can now be collapsed down to a compact toggle with a single click, and your display preference is saved across sessions.
- **Quick adverts directly from Live View.** Trigger advertisements without leaving your map: two dedicated header buttons let you fire a Zero-hop advert to your direct neighbours or a Flood advert across the network with a single click.

---

### Français

Donnez de la voix à votre mesh. Cette version transforme la Vue Live en une véritable expérience sensorielle grâce à des effets sonores synthétisés en temps réel, améliore la précision de vos routes grâce au hachage de chemin sur 2 octets par défaut, et met en avant la communauté en affichant l'emblème Meshloom pour les nœuds du réseau.

#### Nouveautés

- **Écoutez le pouls du mesh en direct.** La Vue Live s'enrichit d'une synthèse audio procédurale qui donne vie aux paquets traversant les ondes. Trois ambiances sonores sont au choix : **Bulles** pour une ambiance douce et discrète, **Laser** pour des impulsions spatialisées dont le timbre varie avec le trajet, ou **8-bit** pour une touche rétro d'arcade. Entièrement généré par le navigateur (aucun fichier audio à charger), le son respecte votre champ de vision pour ne pas saturer lorsque du trafic survient hors écran, limite la polyphonie à 4 voix et reste poliment désactivé au chargement de la page pour ne jamais surprendre.
- **Hachage de chemin sur 2 octets par défaut pour des routes plus nettes.** Sur un mesh actif, le hachage sur 1 octet (limité à 256 valeurs) génère de fréquentes collisions qui brouillent l'affichage des routes et des traces. Meshloom bascule désormais automatiquement les radios compatibles en hachage sur 2 octets (jusqu'à 32 sauts parmi 65 536 combinaisons), garantissant une lisibilité bien supérieure. Si vous préférez conserver 1 octet, votre choix est désormais mémorisé d'une reconnexion à l'autre. Un bandeau discret vous signale si votre radio tourne encore en 1 octet, avec un accès direct aux réglages.
- **Repérez les nœuds Meshloom sur la carte Community.** Dans la fenêtre de portée des observateurs ayant capté votre message, les nœuds propulsés par Meshloom affichent désormais fièrement le logo Meshloom à côté de leur nom. Repérez en un clin d'œil vos pairs dans la région !
- **Légende repliable sur la Vue Live.** Gagnez de la place sur votre carte : l'intégralité de la légende (types de paquets et rôles) peut désormais être repliée en une pastille compacte d'un simple clic sur son chevron, et votre préférence est conservée d'une visite à l'autre.
- **Annonces rapides directement depuis la Vue Live.** Déclenchez vos annonces sans quitter la carte des yeux : deux boutons dédiés dans la barre d'en-tête vous permettent d'émettre une annonce Zéro saut (voisins directs) ou une annonce Flood (tout le réseau) d'un simple clic.

---

## [4.13.2] - 2026-09-18

After an in-app upgrade, the next Install click failed. The update helper rewrote the job status as root, so Meshloom could not start another apply.

#### Fixed

* **Install click after an upgrade:** The helper now leaves the update job file owned by `meshloom`. A leftover root-owned file is replaced in place. The next Install click can start.

---

### Français

Après une mise à jour depuis l’UI, le clic Installer suivant échouait. Le helper réécrivait le statut de mise à jour en root, donc Meshloom ne pouvait plus en relancer une.

#### Corrections

* **Clic Installer après une mise à jour :** Le helper laisse désormais le fichier de statut à `meshloom`. Un fichier resté root est remplacé sur place. Le clic Installer suivant peut partir.

---


## [4.13.1] - 2026-09-18

A 4.12 node that clicked Install went dark. The old package disabled the systemd unit on the way out, and nothing started it again.

#### Fixed

* **Service after in-app upgrade:** Upgrading the `.deb` / `.rpm` no longer runs `systemctl disable --now meshloom`. The apply helper starts the unit when the package is in. If a 4.12 → 4.13.0 click already stopped yours: `sudo systemctl enable --now meshloom`. The database is untouched.

---

### Français

Un nœud en 4.12 qui cliquait Installer se retrouvait sans interface. L’ancien paquet désactivait l’unit systemd en partant, et rien ne la relançait.

#### Corrections

* **Service après mise à jour depuis l’UI :** L’upgrade `.deb` / `.rpm` ne fait plus `systemctl disable --now meshloom`. Le helper relance l’unit une fois le paquet posé. Si un clic 4.12 → 4.13.0 a déjà tout arrêté : `sudo systemctl enable --now meshloom`. La base n’est pas touchée.

---

## [4.13.0] - 2026-09-17

#### Added & Changed

* **Desktop Messaging Behavior:** Pressing `Enter` on desktop environments now sends the message, reverting the behavior introduced in 4.11. `Shift+Enter` inserts a line break. Touchscreen devices retain the standard newline behavior.
* **Map Enhancements:** Detailed view for repeater neighbors now displays SNR, distance, and GPS coordinates directly on the map pin. Added a persistent display option that leaves labels visible on all mapped neighbors. Fixed a rendering bug where text-wrapping tooltips would incorrectly collapse.
* **Updates Management:** Added a dedicated settings page (Settings → Updates). Users can now check for updates, install immediately, or schedule automatic updates (specific weekdays and times). Update notifications are now unobtrusive, indicated by a single pulse on the radio status indicator.
* **Radio Location Picker:** Settings → Radio now features an interactive map for setting coordinates. The map centers on existing coordinates, falling back to the Community IATA location, the browser's timezone city, or a global view. Clicking the map automatically populates the coordinate fields.

#### Fixed

* **Raspberry Pi Installer:** Resolved an "illegal instruction" crash for Raspberry Pi 1 and the original Pi Zero (`armv6l` architectures). The installer no longer erroneously attempts to install the 32-bit `armv7l` package and correctly defaults to a source installation.
* **API Rate Limiting:** The Community service now properly handles HTTP 429 responses from the Stats API (limit: 30 upserts/hour/key) by pausing requests until the hourly limit resets, rather than continuously retrying.
* **Airport Search:** The Community airport list now includes latitude and longitude data provided by Stats, which feeds into the new location picker's IATA fallback.

#### Infrastructure & Documentation

* **Raspberry Pi Docs:** Updated the compatibility matrix to clearly define supported installation routes per board (e.g., clarifying that Pi 2 is strictly 32-bit).
* **CI/CD Pipeline (meshloom.app):** Commits modifying user documentation now trigger a webhook to automatically rebuild the companion site.
* **Build Pipeline Optimization:** The 32-bit package build (QEMU) is now decoupled from the main release workflow. Main releases (amd64, arm64, GitHub) deploy first, while the `armhf` build follows asynchronously. Compiled `armv7` wheels are now cached in GHCR, significantly reducing build times for C extensions.

---

### Français

#### Ajouts et Modifications

* **Messagerie sur bureau :** L'appui sur `Entrée` envoie désormais le message. `Maj+Entrée` permet d'insérer un saut de ligne. Les appareils tactiles conservent le saut de ligne sur `Entrée`.
* **Améliorations de la carte :** La vue détaillée des voisins affiche le SNR, la distance et les coordonnées GPS directement sur l'épingle. Ajout d'une option d'affichage permanent des libellés pour tous les voisins cartographiés. Les infobulles s'affichent désormais correctement sans s'écraser.
* **Gestion des mises à jour :** Nouvelle page dédiée (Réglages → Mises à jour) pour vérifier, installer immédiatement ou planifier les mises à jour automatiques (jours de la semaine et heure). La notification de mise à jour est désormais discrète (une seule impulsion sur l'indicateur d'état radio).
* **Sélecteur de position radio :** L'onglet Réglages → Radio intègre désormais une carte interactive. Elle se centre sur les coordonnées existantes, ou à défaut sur l'IATA Community, la ville du fuseau horaire du navigateur, ou une vue globale. Un clic sur la carte remplit automatiquement les champs.

#### Corrections

* **Installateur Raspberry Pi :** Résolution d'un crash sur les Raspberry Pi 1 et Zero d'origine (`armv6l`). L'installateur ne tente plus d'utiliser le paquet 32 bits (`armv7l`) et bascule correctement sur une installation depuis les sources.
* **Rate Limiting (API Stats) :** Le service Community gère correctement les erreurs HTTP 429 (limite de 30 envois/heure/clé). Au lieu de retenter en boucle, il suspend les requêtes jusqu'à la fin de l'heure en cours.
* **Recherche d'aéroports :** La liste des aéroports Community inclut désormais la latitude et la longitude fournies par Stats, utilisées par le nouveau sélecteur de position.

#### Infrastructure & Documentation

* **Documentation Raspberry Pi :** Mise à jour du tableau de compatibilité clarifiant les méthodes d'installation par modèle (ex: le Pi 2 est exclusivement 32 bits).
* **Pipeline CI/CD (meshloom.app) :** Toute modification de la documentation utilisateur déclenche une reconstruction automatique du site web.
* **Optimisation du pipeline de build :** La compilation du paquet 32 bits (sous QEMU) est détachée du flux principal. Les versions amd64, arm64 et GitHub sont publiées en premier, suivies par `armhf`. Les wheels compilés `armv7` sont désormais mis en cache dans GHCR, réduisant drastiquement le temps de compilation des extensions C.
---

## [4.12.2] - 2026-09-17

The armhf package 4.12.1 promised is actually published now.

### Fixed

- **The 32-bit Raspberry Pi package builds.** It is assembled in an emulated armv7 container, which had a compiler but not `make`, so the dependency that builds libsodium stopped it. Nothing else was wrong with it: the rest of 4.12.1 went out as intended while that one job failed on its own.
- **Signing in again after a proxy session expires.** Behind Cloudflare Access or a similar front, an expired session answered every request with a redirect to a login page on another origin. An open page never sees that page, so the interface stayed up with every call failing and no way to sign in. It now notices and offers to reload, which is the only thing such a proxy responds to.

---

### Français

Le paquet armhf annoncé par la 4.12.1 est réellement publié.

#### Corrections

- **Le paquet pour Raspberry Pi 32 bits se construit.** Il est assemblé dans un conteneur armv7 émulé, qui disposait d'un compilateur mais pas de `make`, ce qui bloquait la dépendance chargée de compiler libsodium. Rien d'autre n'était en cause : le reste de la 4.12.1 est sorti normalement pendant que ce seul job échouait.
- **Se reconnecter après l'expiration d'une session au niveau du proxy.** Derrière Cloudflare Access ou équivalent, une session expirée répondait à chaque requête par une redirection vers une page de connexion sur une autre origine. Une page déjà ouverte ne la voit jamais : l'interface restait affichée, tous les appels échouaient, et rien ne permettait de se reconnecter. Elle le détecte désormais et propose de recharger, seule action à laquelle un tel proxy réagit.

---

## [4.12.1] - 2026-09-17

A Raspberry Pi running the 32-bit Raspberry Pi OS can install Meshloom again, and this time from a package.

### Fixed

- **A 32-bit Raspberry Pi installs.** The apt repository held amd64 and arm64, and the installer offered it to any machine with apt without checking whether it carried anything for that architecture. A Pi 3 or a Pi 4 running the 32-bit system ended up with a repository that could not serve it, apt found no candidate, and the install stopped there. It now installs from an armhf package of its own, and the documentation no longer says to reflash in 64-bit.
- **The repository cannot disagree with what it holds.** Its architecture list was written by hand in three places, and one of them missing an entry is exactly what caused the above. It is now read from the packages themselves, and the installer reads it from the repository rather than keeping its own copy.
- **The Raspberry Pi image builds without the Pages signing key**, which was fetched at bake time and failed the build when it was missing.

### Notes

The armhf package is built under emulation, which is slow, so it is attached to the release shortly after the others rather than holding them up. The 64-bit image is still the recommendation for a Pi that can run it: it is faster.

---

### Français

Un Raspberry Pi sous Raspberry Pi OS 32 bits peut de nouveau installer Meshloom, et cette fois depuis un paquet.

#### Corrections

- **Un Raspberry Pi 32 bits s'installe.** Le dépôt apt ne contenait que amd64 et arm64, et l'installeur le proposait à toute machine disposant d'apt sans vérifier qu'il servait cette architecture. Un Pi 3 ou un Pi 4 en 32 bits se retrouvait avec un dépôt incapable de le servir, apt ne trouvait aucun candidat, et l'installation s'arrêtait là. Il s'installe désormais depuis un paquet armhf, et la documentation ne dit plus de reflasher en 64 bits.
- **Le dépôt ne peut plus contredire ce qu'il contient.** Sa liste d'architectures était écrite à la main à trois endroits, et c'est précisément l'oubli de l'une d'elles qui a causé le point ci-dessus. Elle est maintenant lue depuis les paquets eux-mêmes, et l'installeur la lit dans le dépôt au lieu d'en garder une copie.
- **L'image Raspberry Pi se construit sans la clé de signature Pages**, qui était téléchargée au moment de la cuisson et faisait échouer la construction quand elle manquait.

#### Remarques

Le paquet armhf est construit sous émulation, donc lentement : il est attaché à la release peu après les autres plutôt que de les retarder. L'image 64 bits reste la recommandation pour un Pi qui peut la faire tourner, elle est plus rapide.

---

## [4.12.0] - 2026-09-17

This release is about putting Meshloom on a Raspberry Pi and keeping a node current from the app. You flash a card that already has Meshloom on it, you open Settings → About when a newer version is out, and the node installs Meshloom — not the rest of the operating system.

### What's new
- **A Raspberry Pi image you can flash.** A Pi used to mean installing the OS, then Meshloom, then hoping the first boot had a network. `meshloom-rpi-lite-arm64.img.xz` is a Lite 64-bit card with Meshloom already on it, for the Pi 3 and later including the Zero 2 W. The first boot works offline. Wi-Fi, the user and SSH are set in Raspberry Pi Imager 2.0.6 or newer. A screen, if you have one, shows `http://meshloom.local:8000`.
- **About can install the next release.** 4.8 told you a newer version was out. This one can put it on the node, when the apply helper is there — a package install, or Docker that the installer set up. Automatic updates stay off until you turn them on. That path upgrades Meshloom only. It never runs a silent `apt upgrade` of the OS.
- **The other installs keep an honest recipe.** A Home Assistant add-on still updates in Home Assistant. A container or a source tree without a helper still says to update Meshloom by hand. Re-running the installer will put the helper on if it was missing.

### Upgrading

Package and installer-managed Docker nodes pick this up on the next upgrade. An older node that re-runs the one-liner gets the helper if it was missing. Automatic updates stay off. The Pi image is a new file on the GitHub release; flashing it is how you start from a card, not how you upgrade a node that already runs.

---

### Français

Cette version est consacrée à installer Meshloom sur un Raspberry Pi, et à le tenir à jour depuis l'application. Vous flashez une carte qui embarque déjà Meshloom, vous ouvrez Réglages → À propos quand une version plus récente est sortie, et le nœud installe Meshloom — pas le reste du système.

#### Quoi de neuf
- **Une image Raspberry Pi à flasher.** Un Pi voulait dire installer le système, puis Meshloom, puis espérer que le premier démarrage ait un réseau. `meshloom-rpi-lite-arm64.img.xz` est une carte Lite 64-bit avec Meshloom déjà dessus, pour le Pi 3 et plus, Zero 2 W compris. Le premier démarrage marche hors ligne. Le Wi-Fi, l'utilisateur et SSH se règlent dans Raspberry Pi Imager 2.0.6 ou plus récent. Un écran, s'il y en a un, affiche `http://meshloom.local:8000`.
- **À propos peut installer la prochaine release.** La 4.8 vous disait qu'une version plus récente était sortie. Celle-ci peut la poser sur le nœud, quand le helper d'application est là — une install paquet, ou un Docker que l'installateur a mis en place. Les mises à jour automatiques restent éteintes tant que vous ne les activez pas. Ce chemin ne met à jour que Meshloom. Il ne lance jamais un `apt upgrade` silencieux du système.
- **Les autres installs gardent une recette honnête.** Un add-on Home Assistant se met toujours à jour dans Home Assistant. Un conteneur ou une arborescence source sans helper dit encore de mettre à jour Meshloom à la main. Relancer l'installateur pose le helper s'il manquait.

#### Mise à jour

Les nœuds paquet et Docker gérés par l'installateur le reçoivent à la prochaine mise à jour. Un nœud plus ancien qui relance le one-liner récupère le helper s'il manquait. Les mises à jour automatiques restent éteintes. L'image Pi est un nouveau fichier sur la release GitHub ; la flasher, c'est partir d'une carte, pas mettre à jour un nœud qui tourne déjà.

---

## [4.11.0] - 2026-09-17

This release is about living with Meshloom on a foldable, a tablet and a desk. The list and the chat share the screen the way two apps do: you drag the split, you close it when you need the room, and it remembers how you left it. Conversations themselves are a little quieter, and the Meshloom mark finally has a home on the rail.

### What's new
- **The conversation list folds and moves.** A fixed column used to crush a chat or a repeater dashboard on a foldable. The split is now draggable, with a grip in the middle so you can see it is meant to be moved, and you can close the list when you want the whole width. Tablet and desktop each remember their own width, so opening the same instance on a narrow window does not steal the desk layout.
- **Favourites stay in reach.** They wrap onto another row instead of hiding in a sideways scroll, and a chevron puts them away when you want the list back.
- **Radio status lives in one place.** The little "Radio OK" pill in the header said the same thing as the one at the bottom of the rail, and neither said enough. The rail tile now names the transport, and the dialog is where you advertise — flood to tell the whole network you are here, zero-hop for the neighbours in range.
- **Compose is one menu, not three buttons.** Emoji, GIFs and a location sit behind one control, with tabs and a search for emoji the way GIFs already had one. The field just says Message. Send keeps the keyboard open; Enter starts a new line. A message's menu can copy the text.
- **Avatars read as initials.** A hashtag no longer eats the first letter (`#meshloom` is Me, Public is Pu). Two even letters, not a chopped word.
- **An expanded listener on the map keeps its path to itself.** Opening one observer used to draw every other observer's hops at the same time. Collapse, and the whole neighbourhood comes back.
- **The Meshloom mark sits at the top of the desktop rail.** It opens meshloom.app in a new tab, with room beneath it so a reach for Chat cannot miss. Chat is still Chat.

### Upgrading

Nothing to do. The split you set is stored in the browser; an older width is reused until you move it on that screen.

---

### Français

Cette version est consacrée à la vie avec Meshloom sur un pliable, une tablette et un bureau. La liste et la conversation se partagent l'écran comme deux applications : vous déplacez la séparation, vous la fermez quand vous avez besoin de la place, et elle se souvient de comment vous l'avez laissée. Les discussions elles-mêmes sont un peu plus calmes, et le logo Meshloom a enfin une place sur le rail.

#### Quoi de neuf
- **La liste des conversations se plie et se déplace.** Une colonne fixe écrasait un chat ou un tableau de répéteur sur un pliable. La séparation se tire désormais, avec une prise au milieu pour qu'on voie qu'elle est faite pour bouger, et vous pouvez fermer la liste quand vous voulez toute la largeur. Tablette et bureau retiennent chacun leur largeur, si bien qu'ouvrir la même instance dans une fenêtre étroite n'emprunte pas la disposition du bureau.
- **Les favoris restent à portée.** Ils passent sur une autre ligne au lieu de se cacher dans un défilement latéral, et un chevron les range quand vous voulez retrouver la liste.
- **L'état radio habite un seul endroit.** La pastille « Radio OK » du bandeau disait la même chose que celle en bas du rail, et ni l'une ni l'autre n'en disait assez. La tuile du rail nomme maintenant le transport, et c'est dans la fenêtre que vous annoncez — flood pour dire à tout le réseau que vous êtes là, zéro saut pour les voisins à portée.
- **Composer, c'est un menu, plus trois boutons.** Emoji, GIF et un lieu se tiennent derrière une seule commande, avec des onglets et une recherche d'emoji comme les GIF en avaient déjà une. Le champ dit simplement Message. Envoyer garde le clavier ouvert ; Entrée commence une nouvelle ligne. Le menu d'un message peut copier le texte.
- **Les avatars se lisent comme des initiales.** Un dièse ne mange plus la première lettre (`#meshloom` donne Me, Public donne Pu). Deux lettres égales, pas un mot coupé.
- **Un écoutant déplié sur la carte garde son chemin pour lui.** Ouvrir un observateur dessinait en même temps les sauts de tous les autres. Refermez, et tout le voisinage revient.
- **Le logo Meshloom siège en haut du rail desktop.** Il ouvre meshloom.app dans un nouvel onglet, avec de la place en dessous pour qu'un geste vers Discussions ne puisse pas le rater. Discussions reste Discussions.

#### Mise à jour
Rien à faire. La séparation que vous réglez est stockée dans le navigateur ; une ancienne largeur est réutilisée jusqu'à ce que vous la bougiez sur cet écran.

---

## [4.10.1] - 2026-09-16

Publishing works again after the project moved to a new GitHub owner. No change to the application itself.

### Fixed

- **Images publish again.** The image name was built from the repository path, which is fine until an owner has a capital letter in it. `TwinRocket` does, a registry reference must be lowercase, and every build failed the moment the project moved. Nothing had been published since, which left the Home Assistant add-on pointing at an image that did not exist.
- **The dependency layer survives a release.** A release changes one line in `pyproject.toml` and one in `uv.lock`, its own version, and those two files decided whether the build could reuse what it built last time. It could not, so every release recompiled eight C extensions under emulation for armv7: half an hour, measured, for a string the layer never reads.

### Upgrading

Update to 4.10.1 if you install from the Home Assistant add-on store or pull the image directly. 4.10.0 shipped the same application; it is the publishing around it that was broken.

---

### Français

La publication fonctionne de nouveau après le changement de propriétaire du dépôt GitHub. Aucun changement dans l'application elle-même.

#### Corrections

- **Les images se publient de nouveau.** Le nom de l'image était construit à partir du chemin du dépôt, ce qui marche tant qu'aucune majuscule ne s'y trouve. `TwinRocket` en contient, une référence de registre doit être en minuscules, et tous les builds ont échoué dès le déplacement du projet. Plus rien n'était publié depuis, ce qui laissait l'add-on Home Assistant pointer sur une image inexistante.
- **La couche de dépendances survit à une release.** Une release change une ligne dans `pyproject.toml` et une dans `uv.lock`, sa propre version, et ces deux fichiers décidaient si le build pouvait réutiliser ce qu'il avait construit la fois précédente. Il ne le pouvait pas, donc chaque release recompilait huit extensions C sous émulation pour armv7 : une demi-heure, mesurée, pour une chaîne que cette couche ne lit jamais.

#### Mise à jour

Passez en 4.10.1 si vous installez depuis le magasin d'add-ons Home Assistant ou si vous tirez l'image directement. La 4.10.0 embarquait la même application, c'est la publication autour qui était cassée.

---

## [4.10.0] - 2026-09-16

Meshloom says what to do when it has no radio yet, and the Home Assistant add-on stops looking abandoned.

### What's new

- **A new installation tells you what it needs.** Until a radio is configured there are no contacts, no messages and no packets, so every view was empty for a reason none of them could explain. A banner now says so and opens the radio settings. It appears only while the server reports no transport at all, and never covers the settings page it points at.
- **The add-on has an icon, a logo and a description.** It appeared in the Home Assistant store as a grey placeholder with one line of text. Its page now says what Meshloom does before it says how to install it.

### Under the hood

- **Release images build in a fraction of the time.** A commit-dependent value sat above the dependency layer in the Dockerfile, so every push rebuilt it — which on armv7 means compiling eight C extensions under emulation, measured at 21 minutes 47 seconds per build. It now rebuilds only when the dependencies actually change. Each architecture also builds on its own runner instead of sharing one, and arm64 no longer runs under emulation at all.
- **A test that failed at random no longer does.** It reused a lock left behind by another test, which made it fail depending on which worker it landed on rather than on anything in the code.

### Upgrading

Nothing to do. The banner only appears where there is genuinely nothing configured, and an older server that does not report the field is left alone rather than assumed to be broken.

---

### Français

Meshloom dit quoi faire quand aucune radio n'est configurée, et l'add-on Home Assistant cesse d'avoir l'air à l'abandon.

#### Quoi de neuf

- **Une installation neuve annonce ce qui lui manque.** Tant qu'aucune radio n'est configurée, il n'y a ni contacts, ni messages, ni paquets : chaque vue était vide pour une raison qu'aucune d'elles ne pouvait expliquer. Un bandeau le dit désormais et ouvre les réglages radio. Il n'apparaît que si le serveur signale l'absence totale de transport, et jamais par-dessus la page vers laquelle il renvoie.
- **L'add-on a une icône, un logo et une description.** Il apparaissait dans le magasin Home Assistant comme un cadre gris avec une ligne de texte. Sa page dit maintenant ce que fait Meshloom avant d'expliquer comment l'installer.

#### Sous le capot

- **Les images de release se construisent bien plus vite.** Une valeur dépendant du commit se trouvait au-dessus de la couche de dépendances dans le Dockerfile : chaque push la reconstruisait, ce qui sur armv7 signifie compiler huit extensions C sous émulation — 21 minutes 47 secondes par build, mesurées. Elle n'est plus reconstruite que lorsque les dépendances changent réellement. Chaque architecture se construit en outre sur sa propre machine au lieu de les partager, et arm64 ne passe plus du tout par l'émulation.
- **Un test qui échouait au hasard ne le fait plus.** Il réutilisait un verrou laissé par un autre test, ce qui le faisait échouer selon la machine sur laquelle il tombait plutôt que selon le code.

#### Mise à jour

Rien à faire. Le bandeau n'apparaît que là où rien n'est réellement configuré, et un serveur plus ancien qui ne renseigne pas le champ est laissé tranquille plutôt que supposé en panne.

---

## [4.9.1] - 2026-09-16

The Home Assistant add-on's panel opened blank. It works now.

### What's new

- **The add-on panel shows Meshloom.** Opening it from the sidebar gave a blank panel while the log showed a perfectly healthy start. Home Assistant serves an add-on inside a frame, and Meshloom refused to be framed at all — the browser gave up before it asked for anything. It now accepts being framed by a page on the same address, which is what Home Assistant does, and refuses everything else as before.
- **Meshloom can have an address of its own.** The panel is served under Home Assistant's own address, which means it cannot have a hostname of its own — and Web Push needs one. The add-on's **Network** panel now offers the web port. It is blank by default and nothing is opened until a port is filled in; set one, point a tunnel at it, and set `public_url` to the hostname you chose. Use `basic_auth_username` / `basic_auth_password` unless the tunnel authenticates for you.

### Upgrading

Update the add-on and reload the panel. Nothing changes for any other installation: the framing allowance is off unless the host asks for it, and the add-on is the only thing that does.

---

### Français

Le panneau de l'add-on Home Assistant s'ouvrait vide. C'est réparé.

#### Quoi de neuf

- **Le panneau de l'add-on affiche Meshloom.** L'ouvrir depuis la barre latérale donnait un panneau vide alors que le journal montrait un démarrage parfaitement sain. Home Assistant sert un add-on dans un cadre, et Meshloom refusait tout encadrement — le navigateur renonçait avant même de demander quoi que ce soit. Il accepte désormais d'être encadré par une page de la même adresse, ce que fait Home Assistant, et refuse tout le reste comme avant.
- **Meshloom peut avoir une adresse à lui.** Le panneau est servi sous l'adresse de Home Assistant, donc il ne peut pas avoir de nom d'hôte propre — et le Web Push en exige un. Le panneau **Réseau** de l'add-on propose maintenant le port web. Il est vide par défaut et rien n'est ouvert tant qu'aucun port n'y est mis ; renseignez-en un, faites pointer un tunnel dessus, et mettez dans `public_url` le nom d'hôte choisi. Utilisez `basic_auth_username` / `basic_auth_password` à moins que le tunnel n'authentifie à votre place.

#### Mise à jour

Mettez l'add-on à jour et rechargez le panneau. Rien ne change pour les autres installations : l'autorisation d'encadrement reste inactive tant que l'hôte ne la demande pas, et l'add-on est seul à le faire.

---

## [4.9.0] - 2026-09-16

Meshloom installs as a Home Assistant add-on, and now runs on a Raspberry Pi 3.

### What's new

- **Install it from Home Assistant.** If Home Assistant already runs on the machine that will host Meshloom, add the repository to its add-on store and install Meshloom there. The interface arrives in the sidebar, the database is kept with the rest of Home Assistant's data, and it starts and stops with it. The install guide has a one-click button for adding the repository.
- **A Raspberry Pi 3 is enough.** Images are now built for armv7 alongside the 64-bit ones, so the older Pi that many Home Assistant installations run on is covered.
- **The radio proxy port belongs to whoever publishes it.** Inside the add-on, Home Assistant decides which port is reachable, so Meshloom shows the port and says where to change it rather than accepting a value that would leave the proxy listening where nothing is forwarded. Nothing changes for any other installation.
- **An address can be stated rather than guessed.** `MESHCORE_PUBLIC_URL` tells Meshloom the address it is reached at from outside. Proxy headers describe the hop that happened to arrive, and a tunnel can rewrite or strip them; when it does, links built from what arrived point somewhere reachable only from inside. Setting it settles the question once.

### Upgrading

Nothing to do, and nothing changes for an existing installation: both new settings do nothing unless set, and the add-on is a way to install in addition to the ones that exist rather than a change to them.

Release images take longer to build now, since armv7 is compiled under emulation.

---

### Français

Meshloom s'installe comme add-on Home Assistant, et tourne désormais sur un Raspberry Pi 3.

#### Quoi de neuf

- **Installation depuis Home Assistant.** Si Home Assistant tourne déjà sur la machine qui hébergera Meshloom, ajoutez le dépôt à sa boutique de modules complémentaires et installez Meshloom depuis là. L'interface arrive dans la barre latérale, la base est conservée avec le reste des données de Home Assistant, et Meshloom démarre et s'arrête avec lui. Le guide d'installation contient un bouton pour ajouter le dépôt en un clic.
- **Un Raspberry Pi 3 suffit.** Les images sont maintenant construites pour armv7 en plus des architectures 64 bits, ce qui couvre le Pi plus ancien sur lequel tournent beaucoup d'installations Home Assistant.
- **Le port du proxy radio appartient à qui le publie.** Dans l'add-on, c'est Home Assistant qui décide du port joignable : Meshloom affiche donc le port et indique où le changer, au lieu d'accepter une valeur qui laisserait le proxy à l'écoute là où rien n'est redirigé. Rien ne change pour les autres installations.
- **Une adresse peut être déclarée plutôt que devinée.** `MESHCORE_PUBLIC_URL` indique à Meshloom l'adresse à laquelle il est joint depuis l'extérieur. Les en-têtes de proxy décrivent le saut qui est arrivé, et un tunnel peut les réécrire ou les supprimer ; les liens construits à partir de ce qui est arrivé pointent alors vers un endroit joignable seulement de l'intérieur. La renseigner tranche la question une fois pour toutes.

#### Mise à jour

Rien à faire, et rien ne change pour une installation existante : les deux nouveaux réglages n'ont aucun effet tant qu'ils ne sont pas renseignés, et l'add-on est une façon d'installer en plus des existantes, pas une modification de celles-ci.

Les images de release mettent désormais plus longtemps à se construire, armv7 étant compilé sous émulation.

---

## [4.8.0] - 2026-09-16

This release is about the live map. It now keeps going for as long as you leave it open, it draws every kind of traffic in its own colour, and it holds on to what arrives in a rush instead of letting it go. Meshloom also tells you when a newer version is out.

### What's new

- **The live map stays alive.** Until now it filled up quietly: within a minute of opening it, the space it keeps for animations was taken by packets that were queued to be drawn and never got their turn, so the map went still while traffic kept flowing in. The longer you left the tab open, the emptier it looked. It now keeps pace for as long as you watch it.
- **Every kind of packet has its own colour.** Half the traffic used to arrive in the same neutral grey, simply because only five of the network's message kinds had a colour of their own. Requests, replies, group messages, route announcements, traces and the rest are now each recognisable at a glance, and grey has gone back to meaning what it should: something we genuinely could not identify.
- **A busy mesh no longer loses packets on the way to the screen.** When more arrived at once than the map could animate, the surplus was dropped. It now waits its turn and is drawn a moment later, and the oldest is only ever let go once the wait itself gets too long.
- **Hiding a packet kind no longer empties the map.** Filtering the map down to what interests you used to work against you: the hidden traffic still took up the room reserved for drawing, and within seconds nothing else could appear. Filters now do only what you asked of them.
- **The listeners panel tells the truth about how far a message travelled.** For every node that reported hearing a message, the panel showed the same hop count, taken from the first copy of the packet anyone reported rather than from each listener's own account of the route it took. Nodes hundreds of kilometres apart were all credited with hearing it directly. Each listener now carries the route it actually reported, so the hop counts differ from one to the next the way they always should have.
- **A node that never announced its position is no longer placed off the coast of Africa.** A radio with no GPS fix broadcasts zeroes, and zero is a real place on a map: the point where the equator meets the Greenwich meridian, in the Gulf of Guinea. Around one node in twenty was being pinned there, which put a listener 5,400 km from a message it had heard from next door and made the longest-distance figure meaningless. A radio that has not said where it is now simply has no pin.
- **Meshloom tells you when an update is available.** A discreet notice appears when a newer release has been published, with a link to what changed. Instances that have opted out of Community still get it — a fix is worth knowing about either way.

---

### Français

Cette version est consacrée à la carte live. Elle tient désormais la distance aussi longtemps que vous la laissez ouverte, elle dessine chaque type de trafic dans sa propre couleur, et elle conserve ce qui arrive en rafale au lieu de le laisser filer. Meshloom vous signale aussi la sortie d'une nouvelle version.

#### Quoi de neuf

- **La carte live ne s'endort plus.** Jusqu'ici elle se remplissait en silence : une minute après son ouverture, la place qu'elle réserve aux animations était occupée par des paquets en attente de dessin qui n'obtenaient jamais leur tour, si bien que la carte se figeait alors que le trafic continuait d'arriver. Plus l'onglet restait ouvert, plus elle paraissait vide. Elle suit maintenant le rythme aussi longtemps que vous la regardez.
- **Chaque type de paquet a sa couleur.** La moitié du trafic arrivait dans le même gris neutre, simplement parce que cinq types de messages seulement disposaient d'une couleur propre. Les requêtes, les réponses, les messages de groupe, les annonces de route, les traces et les autres sont désormais reconnaissables d'un coup d'œil, et le gris a retrouvé son sens : quelque chose que nous n'avons vraiment pas su identifier.
- **Un mesh chargé ne perd plus de paquets en route vers l'écran.** Quand il en arrivait plus d'un coup que la carte ne pouvait en animer, le surplus était abandonné. Il attend maintenant son tour et se dessine un instant plus tard ; seul le plus ancien finit par être laissé de côté, et uniquement si l'attente devient trop longue.
- **Masquer un type de paquet ne vide plus la carte.** Filtrer la carte pour ne garder que ce qui vous intéresse jouait contre vous : le trafic masqué occupait toujours la place réservée au dessin, et en quelques secondes plus rien d'autre ne pouvait apparaître. Les filtres ne font désormais que ce que vous leur demandez.
- **Le panneau des écoutes dit la vérité sur la distance parcourue par un message.** Pour chaque nœud ayant signalé avoir entendu un message, le panneau affichait le même nombre de sauts, tiré de la première copie du paquet signalée par quiconque plutôt que du trajet rapporté par chaque écoutant. Des nœuds séparés de centaines de kilomètres se voyaient tous crédités d'une écoute directe. Chaque écoutant porte désormais le trajet qu'il a réellement rapporté, si bien que les nombres de sauts diffèrent de l'un à l'autre comme ils auraient toujours dû le faire.
- **Un nœud qui n'a jamais annoncé sa position n'est plus placé au large de l'Afrique.** Une radio sans point GPS diffuse des zéros, et zéro est un lieu réel sur une carte : l'endroit où l'équateur croise le méridien de Greenwich, dans le golfe de Guinée. Environ un nœud sur vingt s'y retrouvait épinglé, ce qui plaçait un écoutant à 5 400 km d'un message qu'il avait entendu d'à côté et rendait la distance maximale dénuée de sens. Une radio qui n'a pas dit où elle se trouve n'a désormais tout simplement pas de repère.
- **Meshloom vous signale les mises à jour.** Un message discret apparaît lorsqu'une version plus récente a été publiée, avec un lien vers ce qui a changé. Les instances qui ont refusé Community le reçoivent aussi : un correctif mérite d'être connu dans tous les cas.

---

## [4.7.6] - 2026-09-15

The unread badge could show a conversation waiting while the unread filter found none to open.

### What's new

- **The unread badge and the unread filter now count the same thing.** The badge counted how many unread counters were not zero, while the filter counted conversations it could actually put on screen. A counter can outlive what it belonged to — a channel that was deleted, a contact this client has not loaded — so the badge could advertise a conversation nobody was able to open. Both now start from the conversations themselves.

---

### Français

Le badge de non-lus pouvait annoncer une conversation en attente alors que le filtre « Non lues » n'en trouvait aucune à ouvrir.

#### Quoi de neuf

- **Le badge de non-lus et le filtre « Non lues » comptent désormais la même chose.** Le badge comptait les compteurs non nuls, tandis que le filtre comptait les conversations qu'il pouvait réellement afficher. Un compteur peut survivre à ce à quoi il appartenait — un canal supprimé, un contact que ce client n'a pas chargé — si bien que le badge pouvait annoncer une conversation que personne ne pouvait ouvrir. Les deux partent maintenant des conversations elles-mêmes.

---

## [4.7.5] - 2026-09-15

The conversation list said how the last message was encoded rather than what it was.

### What's new

- **A GIF, a location pin or a reaction is named in the conversation list.** These travel across the mesh as ordinary text — a GIF is sent as `g:` followed by an id — and the conversation has always drawn them as a picture, a map or an emoji. The list showed the text as it arrived, so a conversation whose last message was a GIF read `g:APqEbxBsVIkWSuFpth`. It now reads GIF, a pin shows the name its sender gave the place, and a reaction shows its emoji. An ordinary message is left exactly as written.

---

### Français

La liste des conversations disait comment le dernier message était encodé plutôt que ce qu'il était.

#### Quoi de neuf

- **Un GIF, une position ou une réaction sont nommés dans la liste des conversations.** Ces contenus circulent sur le mesh sous forme de texte ordinaire — un GIF s'envoie sous la forme `g:` suivi d'un identifiant — et la conversation les a toujours dessinés comme une image, une carte ou un emoji. La liste, elle, affichait le texte tel qu'il arrivait : une conversation dont le dernier message était un GIF affichait `g:APqEbxBsVIkWSuFpth`. Elle affiche désormais GIF, une position montre le nom que son expéditeur a donné au lieu, et une réaction montre son emoji. Un message ordinaire reste exactement tel qu'il a été écrit.

---

## [4.7.4] - 2026-09-15

Opening the packet analyser on a phone left no way out of it.

### What's new

- **A dialog can no longer open underneath the status bar.** The packet analyser's title and its close control ended up in the strip iOS draws its own status bar over: blurred, out of reach, and with nothing else to close the screen with. Dialogs are now bounded by the space the device actually leaves them, so what they put at the top stays reachable.

---

### Français

Ouvrir l'analyseur de paquet sur un téléphone ne laissait aucun moyen d'en sortir.

#### Quoi de neuf

- **Une fenêtre ne peut plus s'ouvrir sous la barre d'état.** Le titre de l'analyseur de paquet et son bouton de fermeture se retrouvaient dans la bande où iOS dessine sa propre barre d'état : flous, hors d'atteinte, et sans autre moyen de quitter l'écran. Les fenêtres sont désormais bornées par la place que l'appareil leur laisse réellement, de sorte que ce qu'elles placent en haut reste accessible.

---

## [4.7.3] - 2026-09-15

Two screens could not be left. Message search and the repeater dashboard take the whole screen on a phone, and neither offered a way back to where you came from.

### What's new

- **Message search and the repeater dashboard can be left again.** Both fill the screen on a phone, and neither had a back control: once inside, the only way out was the browser's own gesture, which an installed app does not have. Both now carry the same control every other screen does.
- **The proxy settings say where their values go.** Every other group of settings states whether it writes to the radio, to Meshloom or to this browser, and whether the change applies at once. This one gave only its name, which is the one thing you already knew.

---

### Français

Deux écrans ne pouvaient pas être quittés. La recherche de messages et le tableau de bord d'un répéteur occupent tout l'écran sur un téléphone, et ni l'un ni l'autre ne proposait de revenir d'où l'on venait.

#### Quoi de neuf

- **On peut de nouveau quitter la recherche de messages et le tableau de bord d'un répéteur.** Les deux remplissent l'écran sur un téléphone et n'avaient aucun bouton de retour : une fois dedans, il ne restait que le geste du navigateur, dont une application installée ne dispose pas. Ils portent désormais le même contrôle que tous les autres écrans.
- **Les réglages du proxy disent où vont leurs valeurs.** Chaque autre groupe de réglages indique s'il écrit dans la radio, dans Meshloom ou dans ce navigateur, et si le changement s'applique immédiatement. Celui-ci ne donnait que son nom, c'est-à-dire la seule chose que vous saviez déjà.

---

## [4.7.2] - 2026-09-14

This release rebuilds the desktop around three columns, gives the left rail a configurable set of entries, and moves interface preferences off each browser and onto the instance, so the same Meshloom looks the same from every device reaching it.

### What's new

- **The desktop is a rail, a list, and the content.** The single 240px column that mixed the tools with the conversations is gone: with thirty channels the tools left the screen entirely, and every row truncated the name, the preview and the time together. The rail offers the same destinations the phone's bar does, so a place added to one can no longer be missing from the other.
- **The column beside the rail belongs to what you are looking at.** Conversations while you are in conversations, the section list while you are in settings, and nothing at all for the map and the tools — each of those is one full-width view, and the list was taking 384px from panes that lay out two columns of their own.
- **Every tool has its own place on the rail**, and you decide which ones and in what order. Settings → Navigation arranges it: move an entry up or down, take one off, put it back. It starts with everything on it, since removing what you do not use is easier than discovering what you never saw.
- **Interface preferences follow you between devices.** The rail's arrangement and the colour theme are kept with the instance rather than in each browser, so setting them on a computer is enough for the phone that reaches the same Meshloom.
- **The theme no longer flashes on load.** The page is served with the theme already applied instead of being told afterwards, and following the operating system is answered before any script runs. A device seeing the instance for the first time corrects itself once and then never again.
- **The radio status says what it knows.** The dot was enough to notice something was wrong and never enough to act on it: it opens a read-out of the radio's state, how this app is reaching it, which radio and firmware answered, and whether a proxy is in the way — without offering to change anything.
- **Android gets its own chrome.** Installed on Android, the bar runs edge to edge with the indicator behind the icon, the back control is a plain arrow, writing a new message is a floating button, and sending is a circle — the platform's conventions rather than another platform's.
- **Leaving settings takes you where you asked.** Choosing a destination from settings could return you to whatever was open beforehand instead.

### Note for upgrades

This release adds a column to the settings table. The change is additive and nothing is rewritten, but the upgrade does touch the database, so take a copy of it first if you keep one.

---

### Français

Cette version reconstruit le desktop autour de trois colonnes, rend la barre de gauche configurable, et sort les préférences d'interface de chaque navigateur pour les rattacher à l'instance : le même Meshloom se présente de la même façon depuis tous vos appareils.

#### Quoi de neuf

- **Le desktop, c'est une barre, une liste et le contenu.** L'unique colonne de 240 px qui mélangeait les outils et les conversations disparaît : avec trente canaux, les outils sortaient de l'écran, et chaque ligne tronquait ensemble le nom, l'aperçu et l'heure. La barre propose les mêmes destinations que celle du téléphone, si bien qu'un endroit ajouté à l'une ne peut plus manquer à l'autre.
- **La colonne à côté de la barre appartient à ce que vous regardez.** Les conversations quand vous y êtes, la liste des sections dans les réglages, et rien du tout pour la carte et les outils — chacun est une vue pleine largeur, et la liste prenait 384 px à des panneaux qui déploient déjà deux colonnes à eux.
- **Chaque outil a sa place dans la barre**, et vous choisissez lesquels et dans quel ordre. Réglages → Navigation s'en charge : monter, descendre, retirer, remettre. Elle démarre avec tout, parce que retirer ce dont on ne se sert pas est plus facile que découvrir ce qu'on n'a jamais vu.
- **Les préférences d'interface vous suivent d'un appareil à l'autre.** L'agencement de la barre et le thème sont conservés avec l'instance plutôt que dans chaque navigateur : les régler sur un ordinateur suffit pour le téléphone qui atteint le même Meshloom.
- **Le thème ne clignote plus au chargement.** La page est servie avec le thème déjà appliqué au lieu d'en être informée après coup, et « suivre le système » trouve sa réponse avant le moindre script. Un appareil qui découvre l'instance se corrige une fois, puis plus jamais.
- **L'état de la radio se raconte.** Le point suffisait à remarquer un problème, jamais à agir dessus : il ouvre un relevé de l'état de la radio, de la manière dont l'application l'atteint, de quel modèle et quel micrologiciel ont répondu, et si un proxy se trouve sur le chemin — sans rien proposer de modifier.
- **Android a son propre habillage.** Installée sur Android, la barre va d'un bord à l'autre avec l'indicateur derrière l'icône, le retour est une flèche simple, écrire un message est un bouton flottant et envoyer est un rond : les conventions de la plateforme plutôt que celles d'une autre.
- **Quitter les réglages vous emmène où vous avez demandé.** Choisir une destination depuis les réglages pouvait vous ramener à ce qui était ouvert auparavant.

#### À noter pour la mise à jour

Cette version ajoute une colonne à la table des réglages. Le changement est additif et rien n'est réécrit, mais la mise à jour touche bien la base : si vous en gardez une copie, faites-la avant.

---

## [4.7.1] - 2026-09-14

A client connecting to a Meshloom proxy could be told the radio had been swapped, and offered to erase its mesh history to adopt a node that does not exist.

### What's new

- **A proxy no longer claims an identity it does not have.** While a Meshloom is still coming up it knows neither its radio's key nor its own stored one, and it answered connecting clients with a key of all zeros and the name "Meshloom". That is a well-formed key, so the client compared it against the identity it was bound to, found it different, and offered to erase every mesh contact and message in order to adopt it. The proxy now answers with its real key, or with an error while it has none — a client that cannot be told the truth is told nothing.
- **An all-zero key is treated as no key at all.** The other half of the same fix, on the receiving side: a client refuses to read zeros as an identity whatever sent them, so it waits instead of proposing to erase anything. A radio whose key is genuinely different still raises the same warning it always did.

---

### Français

Un client se connectant à un proxy Meshloom pouvait s'entendre dire que la radio avait été remplacée, et se voir proposer d'effacer son historique mesh pour adopter un nœud qui n'existe pas.

#### Quoi de neuf

- **Un proxy n'annonce plus une identité qu'il n'a pas.** Pendant qu'un Meshloom démarre, il ne connaît ni la clé de sa radio ni la sienne en base, et il répondait aux clients qui se connectaient par une clé entièrement nulle et le nom « Meshloom ». Cette clé est bien formée : le client la comparait à l'identité à laquelle il est lié, la trouvait différente, et proposait d'effacer tous les contacts et messages mesh pour l'adopter. Le proxy répond désormais avec sa vraie clé, ou par une erreur tant qu'il n'en a pas — un client à qui on ne peut pas dire la vérité ne s'entend rien dire.
- **Une clé entièrement nulle vaut absence de clé.** L'autre moitié du même correctif, côté réception : un client refuse de lire des zéros comme une identité, quel que soit l'émetteur, et attend au lieu de proposer d'effacer quoi que ce soit. Une radio dont la clé est réellement différente déclenche toujours le même avertissement qu'avant.

---

## [4.7.0] - 2026-09-14

This release rebuilds the phone app around a bar at the bottom of the screen, and rebuilds the live map on a new engine. Installed on a phone, Meshloom now fills the screen and stays put.

### What's new

- **The phone app is navigated from the bottom.** Four destinations — conversations, map, tools, settings — on a bar that is always there, instead of a drawer behind a burger. The app header is gone with it: the radio state moved onto the screens that need it, and settings became a destination of its own.
- **Conversations are one list.** Channels and direct conversations sit together, newest first, with search, filters for unread, favourites, groups and direct, and a row of favourites at the top. It is the whole screen, not a drawer over one.
- **Tools have a screen.** The packet feed, the live map, the mesh visualiser, trace, RF locate, message search and the channel finder are a list with a line each saying what they are for. Each opens full screen with a way back.
- **Settings open on an index.** The sections are grouped by where their values go — the radio, the app, the data it keeps — rather than dropping straight into the radio one.
- **Installed on a phone, the app fills the screen and stays put.** The skeleton no longer drifts when you drag it, only content scrolls, and the keyboard no longer leaves the layout a third shorter than the screen for the rest of the session. Double-tapping no longer leaves the page zoomed.
- **A conversation has one scroll.** Reaching the top loads older messages without losing your place, and the composer stays reachable whatever the draft's length or the keyboard's state.
- **The live map is rebuilt.** Packets land on a MapLibre and deck.gl map as they are heard, with trails and hops coloured by packet type rather than grey, and zero-hop ears drawn where traffic was heard directly.
- **The dark map works again.** CARTO's free tiles began answering with an "API KEY REQUIRED" watermark past a certain zoom. The dark basemap is OpenStreetMap now, and it follows your theme without being asked.
- **Both maps open on your nodes.** Arriving frames everything being heard instead of restoring wherever the last visit ended; a control puts the whole mesh back once you have panned away.
- **Through the proxy, your own messages are yours.** A message sent from a connected client was filed under the contact it was sent to, as though they had written it.

---

### Français

Cette version reconstruit l'application téléphone autour d'une barre en bas de l'écran, et refait la carte live sur un nouveau moteur. Installé sur un téléphone, Meshloom occupe maintenant tout l'écran et ne bouge plus.

#### Quoi de neuf

- **L'application téléphone se navigue par le bas.** Quatre destinations — discussions, carte, outils, réglages — sur une barre toujours présente, au lieu d'un tiroir derrière un menu burger. L'en-tête de l'application disparaît avec lui : l'état de la radio a rejoint les écrans qui en ont besoin, et les réglages sont devenus une destination à part entière.
- **Les conversations forment une seule liste.** Salons et conversations directes ensemble, les plus récentes d'abord, avec recherche, filtres non lues / favoris / groupes / directs, et une rangée de favoris en haut. C'est tout l'écran, pas un tiroir par-dessus.
- **Les outils ont leur écran.** Le flux de paquets, la carte live, le visualiseur mesh, trace, la localisation RF, la recherche de messages et le chercheur de canaux forment une liste, chacun avec une ligne qui dit à quoi il sert. Chacun s'ouvre en plein écran avec un retour.
- **Les réglages s'ouvrent sur un index.** Les sections sont groupées selon l'endroit où vont leurs valeurs — la radio, l'application, les données qu'elle conserve — au lieu d'atterrir directement dans celle de la radio.
- **Installée sur un téléphone, l'application occupe tout l'écran et reste en place.** Le squelette ne dérive plus quand on tire la page, seul le contenu défile, et le clavier ne laisse plus la mise en page d'un tiers trop courte pour le reste de la session. Un double tap ne laisse plus la page zoomée.
- **Une conversation n'a qu'un seul défilement.** Arriver en haut charge les messages plus anciens sans perdre sa place, et la zone de saisie reste atteignable quelle que soit la longueur du brouillon ou l'état du clavier.
- **La carte live est refaite.** Les paquets arrivent sur une carte MapLibre et deck.gl au moment où ils sont entendus, avec des traces et des sauts colorés par type de paquet plutôt qu'en gris, et les oreilles zéro-hop dessinées là où le trafic a été entendu en direct.
- **La carte sombre refonctionne.** Les tuiles gratuites de CARTO se sont mises à répondre par un filigrane « API KEY REQUIRED » au-delà d'un certain zoom. Le fond sombre est désormais OpenStreetMap, et il suit votre thème sans qu'on le lui demande.
- **Les deux cartes s'ouvrent sur vos nœuds.** Arriver cadre tout ce qui est entendu au lieu de restaurer l'endroit où s'était arrêtée la visite précédente ; un bouton remet tout le mesh à l'écran une fois qu'on s'en est éloigné.
- **À travers le proxy, vos messages sont les vôtres.** Un message envoyé depuis un client connecté était classé sous le contact à qui il était destiné, comme s'il l'avait écrit.

---

## [4.6.1] - 2026-09-13

This release lets a second client use this radio without unplugging anything. A phone app or another Meshloom can talk to the mesh through this instance, and Web Push stops refusing the https address of your own server.

### What's new

- **A second client, without pulling the cable.** Sharing a radio used to mean unplugging it from one machine and plugging it into another. Settings > Proxy now lets Meshloom present itself as a MeshCore companion over TCP: a phone app or another Meshloom connects to it and sends and receives through this instance, while the radio stays exactly where it is.
- **Connected clients hear everything the radio hears.** Not only channel text. Adverts, paths, acks, traces and direct messages all come through, the same traffic this node overhears.
- **What a connected client cannot touch.** It cannot rename the node, change the radio settings, or take the private key with it. The companion protocol has no password either, so keep this on a network you trust.
- **Two Meshlooms, one identity.** A second Meshloom connecting here has to start on a fresh database, or explicitly adopt this node's identity. And aiming a Meshloom's own radio connection at its own proxy is refused, since it would only be talking to itself.
- **Web Push accepts the address of your own instance.** Putting your server's https address in the VAPID contact field, or leaving the Docker variable empty, used to stop notifications from ever being sent — the library refused the value without saying so. An https address now works, Meshloom keeping only the beginning of it, and an empty variable falls back to the built-in default.

---

### Français

Cette version permet à un second client d'utiliser cette radio sans rien débrancher. Une appli téléphone ou un autre Meshloom peut parler au mesh à travers cette instance, et Web Push cesse de refuser l'adresse https de votre propre serveur.

#### Quoi de neuf

- **Un second client, sans débrancher le câble.** Partager une radio voulait dire la retirer d'une machine pour la brancher sur une autre. Réglages > Proxy permet maintenant à Meshloom de se présenter comme un companion MeshCore en TCP : une appli téléphone ou un autre Meshloom s'y connecte, envoie et reçoit à travers cette instance, et la radio reste là où elle est.
- **Les clients connectés entendent tout ce que la radio entend.** Pas seulement le texte des salons. Les annonces, les chemins, les acks, les traces et les messages directs passent aussi, exactement le trafic que ce nœud capte.
- **Ce qu'un client connecté ne peut pas toucher.** Il ne peut pas renommer le nœud, modifier les réglages radio, ni emporter la clé privée. Le protocole companion n'a pas de mot de passe non plus : gardez tout ça sur un réseau de confiance.
- **Deux Meshloom, une seule identité.** Un second Meshloom qui se connecte ici doit démarrer sur une base neuve, ou adopter explicitement l'identité de ce nœud. Et brancher la radio d'un Meshloom sur son propre proxy est refusé : il ne ferait que se parler à lui-même.
- **Web Push accepte l'adresse de votre propre instance.** Mettre l'adresse https de votre serveur dans le champ de contact VAPID, ou laisser la variable Docker vide, empêchait les notifications de partir : la bibliothèque refusait la valeur sans rien dire. Une adresse https fonctionne maintenant, Meshloom n'en gardant que le début, et une variable vide retombe sur la valeur par défaut fournie.

---

## [4.6.0] - 2026-09-13

This release is about the interface rather than the mesh. The visualizer gets its graph back, the radio settings page stops hiding which button saves what, the repeater dashboard remembers what it was told, and a good part of the app finally fits on a phone.

### What's new

- **The mesh visualizer shows the mesh.** The control panels sat on top of the graph and covered more than half of it on a laptop — all of it on a phone — and the only way to close them was a checkbox inside the panel, which then left it hovering over the nodes anyway. Controls now live in a toolbar above the graph, grouped by what they do, and open one at a time. Touching the graph closes them. The visualizer was also drawing the same graph twice, one copy permanently invisible; it now draws it once.
- **The radio settings page says what each Save covers.** Two identical-looking checkboxes sat side by side: one was saved by a button further up the page, the other saved itself the moment you ticked it. Ticking the first and leaving the page lost the change without a word. Settings are now grouped by where they are written — to the radio, to Meshloom, or to this browser — each group carries its own Save, greyed out until you change something in that group, and the page splits into tabs so sending an advert no longer means scrolling past five screens.
- **The repeater dashboard opens on what it already knows.** Reloading emptied all nine panels, and the only way to see them again was to ask the repeater over the air — for its name, its radio settings, its regions, things that rarely change. The last answer is kept along with the time it arrived, so the page opens on it and only queries the mesh when you ask. It also stops sending you back to the login form on every reload.
- **The neighbours map is legible and framed on your repeater.** The links to zero-hop neighbours were drawn so faintly they disappeared over the map, and the map opened zoomed to street level on your repeater alone. It now opens centred on your repeater, zoomed out enough to show most of its neighbours, with links coloured by signal. You can switch between the map and the list, refresh either, and expand the map to full screen on a phone.
- **Loading everything tells you where it is.** Panels are fetched one at a time, because the radio answers one question at a time. Only one used to show anything; the other eight looked like panels nobody had asked for. Each now says whether it is being fetched or waiting its turn, the header counts the run down, and you can stop it. Refreshing a panel no longer wipes the values it was showing.
- **Buttons are readable.** Primary buttons were painted with the brand gradient, and white text on its cyan end was close to invisible. They are now solid, with text that passes accessibility contrast. The gradient stays where it belongs, on the logo.
- **The interface fits on a phone.** The theme button sat just outside the screen edge on every page. The conversation filter scrolled away with the list it filters. Opening a settings section from a link showed everything collapsed. All fixed, and every screen was checked at phone, tablet and desktop widths.
- **Empty screens stop promising what cannot come.** The packet feed told you packets would appear in real time while the radio was disconnected. It now says the radio is not connected and points at the control that fixes it.

---

### Français

Cette version concerne l'interface plutôt que le mesh. Le visualiseur rend son graphe, la page des réglages radio cesse de cacher quel bouton enregistre quoi, le tableau de bord d'un répéteur se souvient de ce qu'on lui a dit, et une bonne partie de l'application tient enfin sur un téléphone.

#### Quoi de neuf

- **Le visualiseur mesh montre le mesh.** Les panneaux de contrôle étaient posés sur le graphe et en couvraient plus de la moitié sur un portable — la totalité sur un téléphone — et la seule façon de les fermer était une case à cocher située dans le panneau, qui le laissait ensuite flotter au-dessus des nœuds. Les contrôles sont maintenant dans une barre d'outils au-dessus du graphe, groupés par usage, et s'ouvrent un à la fois. Toucher le graphe les referme. Le visualiseur dessinait aussi deux fois le même graphe, dont une copie invisible en permanence ; il ne le dessine plus qu'une fois.
- **La page des réglages radio dit ce que chaque Enregistrer couvre.** Deux cases d'apparence identique se trouvaient côte à côte : l'une était enregistrée par un bouton situé plus haut dans la page, l'autre s'enregistrait dès qu'on la cochait. Cocher la première et quitter la page perdait le changement sans un mot. Les réglages sont désormais groupés par destination — la radio, Meshloom, ou ce navigateur — chaque groupe porte son propre Enregistrer, grisé tant que rien n'a changé dans ce groupe, et la page se divise en onglets : envoyer une annonce ne demande plus de faire défiler cinq écrans.
- **Le tableau de bord d'un répéteur s'ouvre sur ce qu'il sait déjà.** Un rechargement vidait les neuf panneaux, et le seul moyen de les revoir était d'interroger le répéteur par les ondes — pour son nom, ses réglages radio, ses régions, des valeurs qui changent rarement. La dernière réponse est conservée avec l'heure à laquelle elle est arrivée : la page s'ouvre dessus et n'interroge le mesh que si vous le demandez. Elle ne vous renvoie plus non plus au formulaire de connexion à chaque rechargement.
- **La carte des voisins est lisible et cadrée sur votre répéteur.** Les liens vers les voisins à zéro saut étaient tracés si pâles qu'ils disparaissaient sur la carte, et celle-ci s'ouvrait au niveau de la rue sur votre seul répéteur. Elle s'ouvre maintenant centrée sur lui, dézoomée de quoi montrer la majorité de ses voisins, avec des liens colorés selon le signal. Vous pouvez basculer entre la carte et la liste, rafraîchir l'une ou l'autre, et agrandir la carte en plein écran sur un téléphone.
- **Le chargement complet dit où il en est.** Les panneaux sont récupérés un par un, parce que la radio ne répond qu'à une question à la fois. Un seul le montrait ; les huit autres ressemblaient à des panneaux que personne n'avait demandés. Chacun indique maintenant s'il est en cours de récupération ou en attente de son tour, l'en-tête décompte la progression, et vous pouvez arrêter. Rafraîchir un panneau n'efface plus les valeurs qu'il affichait.
- **Les boutons sont lisibles.** Les boutons principaux étaient peints avec le dégradé de la marque, et le texte blanc sur son extrémité cyan était quasiment invisible. Ils sont désormais unis, avec un texte qui respecte les contrastes d'accessibilité. Le dégradé reste là où il a sa place : sur le logo.
- **L'interface tient sur un téléphone.** Le bouton de thème dépassait du bord de l'écran sur toutes les pages. Le filtre des conversations défilait hors de vue avec la liste qu'il filtre. Ouvrir une section de réglages depuis un lien affichait tout replié. Tout est corrigé, et chaque écran a été vérifié en largeurs téléphone, tablette et bureau.
- **Les écrans vides cessent de promettre l'impossible.** Le flux de paquets annonçait des paquets en temps réel alors que la radio était déconnectée. Il dit maintenant que la radio n'est pas connectée et pointe le contrôle qui règle ça.

---

## [4.5.0] - 2026-09-13

This release keeps polishing the ears on your messages. Your own radio stops counting itself as a listener, a count that turned out wrong is no longer kept for a whole day, and the observer panel finally says something true about messages you received.

### What's new

- **Your own radio is no longer one of the ears.** A radio does not hear its own transmissions, so counting it inflated every badge by one. It is now removed on the server rather than in the interface, which also means the number on the badge and the list in the detail panel can no longer disagree.
- **A count that was wrong is corrected within the hour.** Final counts used to be held for a full day, so a correction made on the directory side could not reach a browser already holding the old answer. They are now held for an hour instead. The directory is still protected — it is what decides a count is final in the first place, not this cache.
- **The observer panel reads correctly on received messages.** It said "your message was heard by these observers" even when the message was not yours. It now simply says "this message".

---

### Français

Cette version continue de peaufiner les oreilles de vos messages. Votre propre radio cesse de se compter comme auditrice, un comptage qui s’avère faux n’est plus conservé une journée entière, et le panneau des observateurs dit enfin quelque chose de juste sur les messages reçus.

#### Quoi de neuf

- **Votre propre radio n’est plus une oreille.** Une radio n’entend pas ses propres émissions : la compter gonflait chaque badge d’une unité. Elle est désormais retirée côté serveur plutôt que dans l’interface, ce qui garantit aussi que le nombre affiché sur le badge et la liste du panneau de détail ne puissent plus diverger.
- **Un comptage erroné est corrigé dans l’heure.** Les comptages définitifs étaient conservés une journée entière, si bien qu’une correction faite côté annuaire ne pouvait pas atteindre un navigateur détenant déjà l’ancienne réponse. Ils ne sont plus conservés qu’une heure. L’annuaire reste protégé : c’est lui qui décide qu’un comptage est définitif, pas ce cache.
- **Le panneau des observateurs est juste sur les messages reçus.** Il annonçait « votre message a été entendu par ces observateurs » même quand le message n’était pas le vôtre. Il dit maintenant simplement « ce message ».

---

## [4.4.0] - 2026-09-13

This release is about the ears on your messages — the badge showing which other radios heard a packet. They come back when they were missing, they stop announcing a direct hop for a node on the other side of the country, and they stop asking the shared directory the same settled question forever.

### What's new

- **Every listener counts again.** The shared directory reports some of the radios it knows without a public key, and Meshloom was quietly discarding exactly those. That is why messages showed no ear at all, or fewer than had really heard them. All of them are counted now, and a radio known through two different directories counts once instead of twice.
- **Distant nodes no longer look like neighbours.** Hop details are passed along as the directory reports them, instead of being rebuilt on the way through, so an observer hundreds of kilometres away is no longer shown as a direct contact at zero hops.
- **A count that fails to load tries again.** If the directory was unreachable on the first attempt, the badge stayed empty until you reloaded the page. Meshloom now retries, spacing the attempts out so an unreachable directory is not hammered.
- **Settled counts stay settled.** A message older than ten minutes will never gain a new listener, so the directory now says when a count is final and Meshloom keeps it. Scrolling back through a long conversation no longer asks again about every message you pass.

Also: a count is only treated as final once an answer has actually been received, so a directory that was briefly down no longer freezes an empty badge in place for good.

---

### Français

Cette version porte sur les oreilles de vos messages — le badge qui indique quelles autres radios ont entendu un paquet. Elles reviennent quand elles manquaient, elles cessent d’annoncer un saut direct pour un nœud à l’autre bout du pays, et elles cessent de reposer indéfiniment à l’annuaire partagé une question déjà tranchée.

#### Quoi de neuf

- **Chaque auditeur compte à nouveau.** L’annuaire partagé signale certaines des radios qu’il connaît sans clé publique, et Meshloom écartait silencieusement celles-là précisément. D’où des messages sans aucune oreille, ou avec moins d’oreilles que de radios les ayant réellement entendus. Toutes sont désormais comptées, et une radio connue via deux annuaires différents compte une fois au lieu de deux.
- **Les nœuds lointains ne passent plus pour des voisins.** Les détails de saut sont transmis tels que l’annuaire les rapporte, au lieu d’être reconstruits au passage : un observateur à des centaines de kilomètres n’est plus présenté comme un contact direct à zéro saut.
- **Un comptage qui échoue réessaie.** Si l’annuaire était injoignable au premier essai, le badge restait vide jusqu’au rechargement de la page. Meshloom réessaie maintenant, en espaçant les tentatives pour ne pas marteler un annuaire hors service.
- **Les comptages tranchés le restent.** Un message de plus de dix minutes ne gagnera jamais un nouvel auditeur : l’annuaire indique donc quand un comptage est définitif, et Meshloom le conserve. Remonter une longue conversation ne réinterroge plus chaque message traversé.

Aussi : un comptage n’est considéré comme définitif qu’une fois une réponse réellement reçue, si bien qu’un annuaire momentanément hors service ne fige plus un badge vide pour de bon.

---

## [4.3.0] - 2026-09-13

This release adds an optional shared directory of nearby nodes, gathers every notification setting into one screen, and makes conversations easier to read.

### What's new

- **A shared directory of the nodes around you.** New databases join Meshloom Community, where this radio can publish the packets it overhears. Pooling those observations is what lets Meshloom name the repeaters a message passed through, show who else heard it, and narrow down where a node is. Set your area by searching for the nearest airport by name in Settings > Community; a banner asks for that code until you save one, and you can dismiss the banner if you would rather not join. Leaving is a single toggle in the same place, and existing databases are never opted in for you.
- **One screen for notifications.** Settings > Notifications now holds your registered devices, what notifies you by default, per-conversation exceptions, and the contact address Apple requires. Notifications come from the browser, so they still arrive when the Meshloom tab is closed; the old in-tab desktop alerts are gone. The bell in a conversation header is now a plain on/off switch, and Meshloom can also tell you the first time it hears a new companion, repeater, or sensor.
- **A channel finder that looks further back.** It now searches the channel packets you already stored but could not read, instead of only what arrived while the page was open. It also tries a bundled list of common MeshCore channel names, plus the hashtag names shared by nodes near your airport when Community is on — names only, never keys.
- **A more readable conversation.** The message column is centered on screen, and the sidebar shows an excerpt of the last message in each thread. Send now refuses a message that is too long for one radio packet instead of quietly cutting it short.

Also: new and upgraded databases get a `#meshloom` channel, and an existing `#remoteterm` is left alone. Dark map tiles accept an optional CARTO API key. Clicking an observer badge no longer hangs on “Loading observers…”.

To create a new database without joining Community: `MESHLOOM_COMMUNITY=0`.

---

### Français

Cette version ajoute un annuaire partagé des nœuds proches, en option, regroupe tous les réglages de notification sur un seul écran et rend les conversations plus lisibles.

#### Quoi de neuf

- **Un annuaire partagé des nœuds autour de vous.** Les nouvelles bases rejoignent Meshloom Community, où cette radio peut publier les paquets qu’elle capte. C’est la mise en commun de ces observations qui permet à Meshloom de nommer les répéteurs traversés par un message, de montrer qui d’autre l’a entendu et de situer approximativement un nœud. Définissez votre zone en cherchant l’aéroport le plus proche par son nom dans Réglages > Community : un bandeau réclame ce code jusqu’à ce que vous l’enregistriez, et vous pouvez le masquer si vous préférez ne pas participer. Quitter tient à un seul interrupteur au même endroit, et les bases existantes ne sont jamais inscrites à votre place.
- **Un seul écran pour les notifications.** Réglages > Notifications regroupe désormais vos appareils enregistrés, ce qui vous prévient par défaut, les exceptions par conversation et l’adresse de contact qu’Apple exige. Les notifications viennent du navigateur : elles arrivent donc même quand l’onglet Meshloom est fermé, et les anciennes alertes affichées dans l’onglet disparaissent. La cloche d’une conversation est maintenant un simple interrupteur, et Meshloom peut aussi vous signaler la première fois qu’il entend un nouveau compagnon, répéteur ou capteur.
- **Un chercheur de salons qui remonte plus loin.** Il fouille maintenant les paquets de salon déjà stockés mais illisibles, au lieu des seuls paquets arrivés pendant que la page était ouverte. Il essaie aussi une liste fournie de noms de salons MeshCore courants, ainsi que les noms de hashtags partagés par les nœuds proches de votre aéroport quand Community est activé — les noms seulement, jamais les clés.
- **Une conversation plus lisible.** La colonne des messages est centrée à l’écran et la barre latérale affiche un extrait du dernier message de chaque fil. L’envoi refuse désormais un message trop long pour un paquet radio, au lieu de le tronquer sans le dire.

Aussi : les bases neuves et mises à jour reçoivent un salon `#meshloom`, et un `#remoteterm` existant est laissé en place. Les tuiles sombres de la carte acceptent une clé d’API CARTO optionnelle. Un clic sur un badge d’observateur ne reste plus bloqué sur « Chargement des observateurs… ».

Pour créer une base neuve sans rejoindre Community : `MESHLOOM_COMMUNITY=0`.

---

## [4.2.1] - 2026-09-13

Observer-reach badges work again when Meshloom Community is on. CoreScope’s `POST /api/packets/observations` is ingest, not a batch query; counts now fall back to per-packet detail instead of hiding every ear.

### Highlights

- Flood-message observer ears come back with Community enabled, even without a manual CoreScope URL

### Fixed

- Bug: Community observer-reach counts treated CoreScope `POST /api/packets/observations` as a query. That route is ingest-only, so the directory call failed and the UI hid every badge. Counts now fall back to `GET` packet detail, the same path hop-name resolution already used

---

### Français

Les badges observateurs refonctionnent quand Meshloom Community est activé. Le `POST /api/packets/observations` de CoreScope est un ingest, pas une requête batch ; les comptages retombent sur le détail par paquet au lieu de cacher toutes les oreilles.

#### Points forts

- Les oreilles sur les messages flood reviennent avec Community, même sans URL CoreScope manuelle

#### Corrections

- Bug : les comptages observateurs Community traitaient le `POST /api/packets/observations` CoreScope comme une requête. Cette route est un ingest, l’appel directory échouait, et l’UI cachait chaque badge. Les comptages retombent maintenant sur le détail `GET` par hash, le même chemin que la résolution des nœuds

---

## [4.2.0] - 2026-09-13

Optional **Meshloom Stats** observer community: join from Settings, bind a 3-letter IATA code, and this server can publish overheard packets to the official Stats hosts. Existing installs stay off until you join.

### Highlights

- New **Settings > Meshloom Stats** pane to join, bind IATA, and see community contribution stats
- One opt-out stops both publish and community directory calls. Directory can fall back to a manual CoreScope URL in Radio-App
- Observer-reach detail can show hop paths and origin when the directory returns them

### Added

- Feature: Opt-in Meshloom Stats settings (`/api/community`), IATA bind, and a hidden system MQTT publisher (not listed in Fanout CRUD)
- Feature: Stats JWT includes `iata`. Existing databases stay opted out; `MESHLOOM_COMMUNITY=1` only seeds new installs
- Feature: Observer-reach views can render hop paths and origin metadata

### Changed

- Misc: Python, React 19, Vitest 5, and Playwright. Tailwind stays on 3; TypeScript stays on 5
- Misc: Backup restore keeps the SQLite dump intact but honest, validates directory restore before writes, and bounds in-memory CoreScope caches

### Fixed

- Bug: Repeater CLI `send_cmd` after meshcore 2.3.9 required a contact type
- Bug: OSM map tiles were blocked; the map now sends a Referer and uses the official tile URL
- Bug: Module-level asyncio locks broke when pytest-asyncio created a new event loop
- Bug: Joining Meshloom Stats did not turn on hop names, locate, or observer-reach UI; those stayed gated on the manual CoreScope toggle

---

### Français

Communauté d’observateurs **Meshloom Stats** en option : rejoindre depuis les Réglages, associer un code IATA à 3 lettres, et ce serveur peut publier les paquets entendus vers les hôtes Stats officiels. Les installs existantes restent hors ligne tant que tu n’as pas rejoint.

#### Points forts

- Nouvel onglet **Réglages > Meshloom Stats** pour rejoindre, associer l’IATA, et voir les stats de contribution
- Un seul opt-out arrête la publication **et** les appels directory communautaires. Le directory peut retomber sur une URL CoreScope manuelle dans Radio-App
- Le détail observateurs peut afficher les chemins de sauts et l’origine quand le directory les fournit

#### Ajouts

- Fonction : réglages Meshloom Stats opt-in (`/api/community`), association IATA, et publisher MQTT système invisible (pas dans le CRUD Fanout)
- Fonction : le JWT Stats inclut `iata`. Les bases existantes restent opt-out ; `MESHLOOM_COMMUNITY=1` ne seed que les nouvelles installs
- Fonction : les vues observateurs peuvent afficher chemins de sauts et métadonnées d’origine

#### Changements

- Divers : Python, React 19, Vitest 5 et Playwright. Tailwind reste en 3 ; TypeScript reste en 5
- Divers : la restauration de backup garde le dump SQLite intact mais honnête, valide le restore directory avant écriture, et borne les caches CoreScope en mémoire

#### Corrections

- Bug : le CLI répéteur `send_cmd` après meshcore 2.3.9 exigeait un type de contact
- Bug : les tuiles OSM étaient bloquées ; la carte envoie un Referer et utilise l’URL officielle
- Bug : les locks asyncio de module cassaient quand pytest-asyncio créait une nouvelle boucle
- Bug : rejoindre Meshloom Stats n’activait pas l’UI des noms de sauts, locate, ni portée observateurs ; elle restait liée au toggle CoreScope manuel

---

## [4.1.3] - 2026-09-11

Settings > Radio always offers USB serial, TCP, and Bluetooth. A missing port or a failed BLE scan no longer hides those choices. Re-running the Linux installer is an upgrade: it remembers the language and names the version change.

### Highlights

- Serial, TCP, and Bluetooth stay visible in **Settings > Radio** even when this host cannot see a device yet
- Saved language on the Linux one-liner, plus an explicit upgrade prompt (`4.1.2` → `4.2.3`)

### Changed

- Misc: Transport capability flags are always selectable. Missing serial ports or a missing Bluetooth adapter are shown as hints, not as a reason to hide the picker
- Misc: Loading radio settings no longer runs a BLE discovery scan. Scan stays on the **Scan** button
- Misc: systemd grants `bluetooth` when that group exists (`SupplementaryGroups`, `After=bluetooth.target`) so a later BLE choice can work
- Misc: Linux installer remembers `en` / `fr` in `installer.conf` and asks to upgrade (or reinstall) instead of “Start the installation?”



### Fixed

- Bug: USB and Bluetooth disappeared from Settings when no `/dev/ttyUSB*` / `/dev/ttyACM*` was listed, or when a 2-second BLE scan timed out
- Bug: Serial “unavailable” copy always talked about Docker. It now mentions `dialout` on a normal host, and container device mapping only inside a container

---



### Français

Réglages > Radio propose toujours le série USB, TCP et Bluetooth. Un port absent ou un scan BLE raté ne masque plus ces choix. Relancer l’installeur Linux est une mise à jour : la langue est mémorisée, et le changement de version est nommé.

#### Points forts

- Série, TCP et Bluetooth restent visibles dans **Réglages > Radio**, même si l’hôte ne voit pas encore de périphérique
- Langue mémorisée sur le one-liner Linux, et une question d’upgrade explicite (`4.1.2` → `4.2.3`)



#### Changements

- Divers : les transports restent sélectionnables. Ports série absents ou pas d’adaptateur Bluetooth = avertissement, pas un bouton caché
- Divers : ouvrir les réglages radio ne lance plus de scan BLE. Le scan reste sur le bouton **Rechercher**
- Divers : systemd ajoute `bluetooth` quand le groupe existe (`SupplementaryGroups`, `After=bluetooth.target`) pour un BLE choisi plus tard
- Divers : l’installeur Linux retient `en` / `fr` dans `installer.conf` et demande une mise à jour (ou une réinstall) au lieu de « Lancer l’installation ? »



#### Corrections

- Bug : USB et Bluetooth disparaissaient des réglages sans `/dev/ttyUSB*` / `/dev/ttyACM*`, ou si un scan BLE de 2 s expirait
- Bug : le texte « série indisponible » parlait toujours de Docker. Il parle de `dialout` sur un hôte normal, et du mapping de device seulement dans un conteneur

---



## [4.1.2] - 2026-09-11

The Linux one-liner actually downloads the GitHub package again before installing it. 4.1.1 named the tempfile `.deb` but skipped the download, so apt tried to install an empty file.

### Highlights

- Service install from the latest published `.deb` / `.rpm` downloads the package again



### Fixed

- Bug: the installer created an empty `/tmp/meshloom.*.deb` and `apt-get install` failed with `could not locate member control.tar` / `read, still have 8 to read but none left`

---



### Français

L’installeur Linux retélécharge le paquet GitHub avant de l’installer. La 4.1.1 nommait le fichier temporaire `.deb` mais sautait le téléchargement, donc apt installait un fichier vide.

#### Points forts

- L’installation service depuis le `.deb` / `.rpm` publié retélécharge le paquet



#### Corrections

- Bug : l’installeur créait un `/tmp/meshloom.*.deb` vide et `apt-get install` échouait avec `could not locate member control.tar` / `read, still have 8 to read but none left`

---



## [4.1.1] - 2026-09-11

The Linux one-liner can install the latest GitHub `.deb` again. Apt was rejecting a valid package because the tempfile had no `.deb` suffix.

### Highlights

- Service install from the latest published `.deb` / `.rpm` works again on Debian, Ubuntu, and Fedora



### Fixed

- Bug: `apt-get install` of the downloaded release package failed with `Unsupported file /tmp/tmp.… given on commandline` because the tempfile had no `.deb` extension

---



### Français

L’installeur Linux peut à nouveau installer le `.deb` GitHub. Apt refusait un paquet valide parce que le fichier temporaire n’avait pas le suffixe `.deb`.

#### Points forts

- L’installation service depuis le `.deb` / `.rpm` publié fonctionne à nouveau sur Debian, Ubuntu et Fedora



#### Corrections

- Bug : `apt-get install` du paquet téléchargé échouait avec `Unsupported file /tmp/tmp.… given on commandline` parce que le fichier temporaire n’avait pas l’extension `.deb`

---



## [4.1.0] - 2026-09-11

Flood messages can now show how many CoreScope MQTT observers heard the same packet, and chat metadata no longer wraps into the text.

### Highlights

- Optional **observer** ear on flood messages when CoreScope is enabled and at least one observer published the packet
- Click the ear for the observer list, hop/distance summary, and a map
- Date, time, observer count, path, and ACK sit on one line under the message body



### Added

- Feature: Persist firmware packet hashes on stored messages (`packet_hash`, `observer_reach_eligible`) so floods can be matched to CoreScope observations
- Feature: `POST /api/directory/packets/reach-counts` and `GET /api/directory/packets/{hash}/reach` — batch counts for visible floods, then a detail photo (list + map). One opted-in CoreScope instance only (`directory_enabled` + `directory_url`)
- Feature: Chat ear badge on channel floods, and on DMs only when a flood echo arrives. Hidden when the count is 0. Young floods refresh every 8s for 1 minute, then every 60s until 10 minutes; outgoing first lookup waits 20s



### Changed

- Misc: Message time, observer ear, hops / ACK, and region badge move to a single metadata row under the body. The sender name stays above incoming first-in-group messages



### Fixed

- Bug: Observer counts are looked up against the virtualizer’s oldest-first list, not the newest-first REST page
- Bug: Replacing the `messages` array no longer cancels in-flight CoreScope count fetches
- Bug: Empty CoreScope batch responses fall back to a per-hash GET so a live count can still appear
- Bug: Observer detail map has a real height and Leaflet CSS, so the dialog is usable

---



### Français

Les messages flood peuvent maintenant montrer combien d’observateurs MQTT CoreScope ont entendu le même paquet, et les métadonnées du chat ne se mélangent plus au texte.

#### Points forts

- Oreille **observateurs** optionnelle sur les floods quand CoreScope est activé et qu’au moins un observateur a publié le paquet
- Clic sur l’oreille : liste des observateurs, résumé sauts / distance, et carte
- Date, heure, nombre d’observateurs, chemin et ACK sur une seule ligne sous le message



#### Ajouts

- Fonction : conservation du hash firmware sur les messages stockés (`packet_hash`, `observer_reach_eligible`) pour les apparier aux observations CoreScope
- Fonction : `POST /api/directory/packets/reach-counts` et `GET /api/directory/packets/{hash}/reach` — comptages par lot pour les floods visibles, puis une photo de détail (liste + carte). Une seule instance CoreScope opt-in (`directory_enabled` + `directory_url`)
- Fonction : pastille oreille sur les floods canal, et sur les DM seulement si un écho flood arrive. Masquée si le compte est 0. Les floods jeunes se rafraîchissent toutes les 8 s pendant 1 minute, puis toutes les 60 s jusqu’à 10 minutes ; le premier lookup sortant attend 20 s



#### Changements

- Divers : l’heure, l’oreille, les sauts / ACK et le badge de région passent sur une seule ligne de métadonnées sous le corps. Le nom de l’expéditeur reste au-dessus des premiers messages d’un groupe entrant



#### Corrections

- Bug : les comptages d’observateurs sont lus sur la liste oldest-first du virtualizer, pas sur la page REST newest-first
- Bug : remplacer le tableau `messages` n’annule plus les lookups CoreScope en cours
- Bug : un lot CoreScope vide bascule sur un GET par hash pour qu’un compte puisse quand même apparaître
- Bug : la carte de détail a une hauteur réelle et le CSS Leaflet, donc le dialogue est utilisable

---



## [4.0.0] - 2026-09-11

First public **Meshloom** release. This is a new product branch of Jack Kingsman’s [Remote Terminal for MeshCore](https://github.com/jkingsman/Remote-Terminal-for-MeshCore), via [Ian Langworth’s 3.19.0 line](https://github.com/statico/remoteterm-meshcore/releases/tag/3.19.0). The original MIT copyright stays in `LICENSE.md`.

Meshloom is the same job as before — an always-on web client for a MeshCore companion radio — with a bilingual UI, a named identity for the database, radio setup in the browser, and a few observation tools the radio cannot do alone.

### Highlights

- English / French UI, toasts, push notifications, and installer
- Radio transport is chosen in **Settings > Radio** (USB, TCP, or BLE). The radio stays paused until that is set
- The database binds to the radio’s public key on first use (or after an upgrade from an unbound 3.x database)
- **RF Locate** draws conservative 0-hop coverage disks. It never invents a lat/lon pin
- Optional **CoreScope** directory to name unknown hops and search nodes
- New Linux installer, `.deb` / `.rpm` packages, and `ghcr.io/bagl3y/meshloom`
- In-browser Web Serial flasher for official MeshCore firmware



### Added

- Feature: Complete EN/FR i18n for the operator UI, toasts, and web push
- Feature: RF Locate (`#locate` / `#locate/{key}`) — unique identity, then 0-hop disks from local hearings and/or CoreScope (`local` / `corescope` / `mixte`). Ambiguous queries return 409 with candidates
- Feature: CoreScope directory proxy — hop resolve (2/3-byte only), node search, reach, and neighbors. Radio contacts stay authoritative over the overlay
- Feature: Local-only contact groups, optional stale-contact purge, and JSON backup/restore of contacts, channels, settings, and groups (plus a SQLite snapshot). The radio private key is never in the backup
- Feature: Richer chat — emoji picker, optional Giphy (browser-local API key), MeshCore Open reactions, hop-count badges, path inspector, and a MeshCore share QR
- Feature: Meshloom-branded Web Serial flasher for official companion / repeater / room-server firmware
- Feature: Linux one-liner installer at `https://get.meshloom.app` (systemd or Docker, EN/FR, root-or-sudo)
- Feature: Native `meshloom` `.deb` / `.rpm` for amd64 and arm64; Docker image `ghcr.io/bagl3y/meshloom`
- Feature: Portainer GitOps compose (`docker-compose.dev.yaml` + `.env.example`)
- Feature: EN/FR user docs in `docs/user/` (published at [https://meshloom.app/docs/](https://meshloom.app/docs/))



### Changed

- Breaking: Project, packages, and paths are Meshloom — image `ghcr.io/bagl3y/meshloom`, unit `meshloom`, data `/var/lib/meshloom`, env `/etc/meshloom/meshloom.env`
- Breaking: Radio transport is no longer configured with `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST`, or `MESHCORE_BLE_ADDRESS`. Set it in the UI
- Breaking: An existing database without `radio_bound_public_key` opens the identity dialog (`identity_unbound_legacy`). Bind without wipe if this is the same radio; wipe mesh contacts/messages if it is a different device. A live key mismatch closes ingest until you adopt or reject
- Breaking: AUR packaging is removed. Use the installer, `.deb` / `.rpm`, or Docker
- Breaking: The installer no longer prompts for bots or HTTP Basic auth. Set `MESHCORE_DISABLE_BOTS` and optional `MESHCORE_BASIC_AUTH_*` in the environment yourself
- Misc: First Meshloom branding (cyan-to-violet theme) and renamed release artifacts (`meshloom-prebuilt-frontend-…`)
- Misc: Installer uses `as_root` / `priv` so it works on root-only hosts without assuming `sudo`
- Misc: Marketing site no longer lives in this repo; the README points at `docs/user/`



### Fixed

- Bug: Identity gate no longer deadlocks the radio lock when setup is paused
- Bug: Background radio sync does not write while the identity gate is closed
- Misc: Drop the unused vendored decoder, dead assets, and orphaned release scripts



### Install

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000) and choose the radio under **Settings > Radio**. Do not pipe the installer into `bash` — it needs a real terminal.

- Docker: `ghcr.io/bagl3y/meshloom` (see `docker-compose.example.yml`)
- Docs: [https://meshloom.app/docs/](https://meshloom.app/docs/)
- Trusted network only. There are no user accounts. Bots can run arbitrary Python unless `MESHCORE_DISABLE_BOTS=true`



### Upgrade from Remote Terminal / 3.19.0

1. Install Meshloom (new image or package). Keep the existing SQLite file if you want the history (`./data` or the old data directory).
2. Set transport in **Settings > Radio**. Env-based serial/TCP/BLE settings are ignored.
3. Confirm the identity dialog. **Previous key: unknown** means the database predates binding, not that the radio changed.
4. Re-apply bot disable / Basic auth in `/etc/meshloom/meshloom.env` or Compose if you still want them.

Meshloom 4.0.0 diverged at 3.19.0. Later 3.x features from other forks are not included.

### Credits

Jack Kingsman built the original client. Ian Langworth carried the 3.x line this release starts from. Thank you both.

---



### Français

Première version publique de **Meshloom**. C’est une nouvelle branche du [client web MeshCore de Jack Kingsman](https://github.com/jkingsman/Remote-Terminal-for-MeshCore), à partir de la [ligne 3.19.0 d’Ian Langworth](https://github.com/statico/remoteterm-meshcore/releases/tag/3.19.0). Le copyright MIT d’origine reste dans `LICENSE.md`.

Meshloom fait le même métier qu’avant — un client web toujours à l’écoute d’une radio compagnon MeshCore — avec une interface bilingue, une identité liée à la base, la radio configurée dans le navigateur, et quelques outils d’observation qu’une radio seule ne peut pas faire.

#### Points forts

- Interface, toasts, notifications push et installeur en anglais / français
- Le transport radio se choisit dans **Réglages > Radio** (USB, TCP ou BLE). La radio reste en pause tant que ce n’est pas fait
- La base se lie à la clé publique de la radio au premier usage (ou après une mise à niveau depuis une base 3.x sans liaison)
- **RF Locate** dessine des disques de couverture 0-saut prudents. Il n’invente jamais un point lat/lon
- Annuaire **CoreScope** optionnel pour nommer les sauts inconnus et chercher des nœuds
- Nouvel installeur Linux, paquets `.deb` / `.rpm`, et image `ghcr.io/bagl3y/meshloom`
- Flasher Web Serial dans le navigateur pour le firmware MeshCore officiel



#### Ajouts

- Fonction : i18n EN/FR complète pour l’interface opérateur, les toasts et le web push
- Fonction : RF Locate (`#locate` / `#locate/{clé}`) — identité unique, puis disques 0-saut à partir des écoutes locales et/ou de CoreScope (`local` / `corescope` / `mixte`). Une requête ambiguë renvoie 409 avec des candidats
- Fonction : proxy d’annuaire CoreScope — résolution de sauts (2/3 octets seulement), recherche de nœuds, portée et voisins. Les contacts radio restent la source de vérité face à l’overlay internet
- Fonction : groupes de contacts locaux, purge optionnelle des contacts trop vieux, et sauvegarde / restauration JSON des contacts, canaux, réglages et groupes (plus un cliché SQLite). La clé privée de la radio n’est jamais dans la sauvegarde
- Fonction : chat plus riche — sélecteur d’emoji, Giphy optionnel (clé API locale au navigateur), réactions MeshCore Open, pastilles de nombre de sauts, inspecteur de chemin, et QR de partage MeshCore
- Fonction : flasher Web Serial aux couleurs Meshloom pour le firmware officiel compagnon / répéteur / serveur de salon
- Fonction : installeur Linux en une ligne sur `https://get.meshloom.app` (systemd ou Docker, EN/FR, root ou sudo)
- Fonction : paquets natifs `meshloom` `.deb` / `.rpm` amd64 et arm64 ; image Docker `ghcr.io/bagl3y/meshloom`
- Fonction : compose GitOps Portainer (`docker-compose.dev.yaml` + `.env.example`)
- Fonction : docs utilisateur EN/FR dans `docs/user/` (publiées sur [https://meshloom.app/docs/](https://meshloom.app/docs/))



#### Changements

- Cassant : le projet, les paquets et les chemins sont Meshloom — image `ghcr.io/bagl3y/meshloom`, unité `meshloom`, données `/var/lib/meshloom`, env `/etc/meshloom/meshloom.env`
- Cassant : le transport radio ne se configure plus avec `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST` ou `MESHCORE_BLE_ADDRESS`. On le règle dans l’interface
- Cassant : une base existante sans `radio_bound_public_key` ouvre le dialogue d’identité (`identity_unbound_legacy`). Lier sans effacer si c’est la même radio ; effacer contacts et messages mesh si c’est un autre appareil. Un décalage de clé en direct ferme l’ingest jusqu’à adoption ou refus
- Cassant : le paquet AUR est retiré. Utiliser l’installeur, les `.deb` / `.rpm`, ou Docker
- Cassant : l’installeur ne demande plus les bots ni l’auth HTTP Basic. Régler soi-même `MESHCORE_DISABLE_BOTS` et, au besoin, `MESHCORE_BASIC_AUTH_*` dans l’environnement
- Divers : première identité visuelle Meshloom (thème cyan–violet) et artefacts de release renommés (`meshloom-prebuilt-frontend-…`)
- Divers : l’installeur utilise `as_root` / `priv` pour fonctionner sur un hôte root-only sans supposer `sudo`
- Divers : le site vitrine n’est plus dans ce dépôt ; le README pointe vers `docs/user/`



#### Corrections

- Bug : le verrou d’identité ne bloque plus le verrou radio quand l’initialisation est en pause
- Bug : la sync radio en arrière-plan n’écrit plus tant que le verrou d’identité est fermé
- Divers : suppression du décodeur vendored inutilisé, des assets morts et des scripts de release orphelins



#### Installation

```bash
/bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
```

Puis ouvrir [http://127.0.0.1:8000](http://127.0.0.1:8000) et choisir la radio dans **Réglages > Radio**. Ne pas piper l’installeur dans `bash` : il a besoin d’un vrai terminal.

- Docker : `ghcr.io/bagl3y/meshloom` (voir `docker-compose.example.yml`)
- Docs : [https://meshloom.app/docs/](https://meshloom.app/docs/)
- Réseau de confiance seulement. Pas de comptes utilisateurs. Les bots peuvent exécuter du Python arbitraire sauf si `MESHCORE_DISABLE_BOTS=true`



#### Mise à niveau depuis Remote Terminal / 3.19.0

1. Installer Meshloom (nouvelle image ou paquet). Garder le fichier SQLite existant pour conserver l’historique (`./data` ou l’ancien répertoire de données).
2. Régler le transport dans **Réglages > Radio**. Les réglages serial/TCP/BLE par variables d’environnement sont ignorés.
3. Confirmer le dialogue d’identité. **Clé précédente** : **Inconnue** signifie que la base précède la liaison, pas que la radio a changé.
4. Remettre la désactivation des bots / l’auth Basic dans `/etc/meshloom/meshloom.env` ou Compose si vous les voulez encore.

Meshloom 4.0.0 a divergé à la 3.19.0. Les fonctions 3.x plus récentes des autres forks ne sont pas incluses.

#### Crédits

Jack Kingsman a écrit le client d’origine. Ian Langworth a porté la ligne 3.x dont part cette version. Merci à tous les deux.
