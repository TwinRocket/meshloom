---
title: Accounts and access
description: Accounts, roles, sessions, trusted networks, tokens, two-factor authentication and passkeys, Home Assistant and the kiosk screen, the 4.x Basic login, the audit log, and meshloom-admin, the command that gets you back in from the machine, for each way of installing Meshloom.
level: deep
order: 21
---

From Meshloom 5.0, nothing is shown before you sign in: every page and every API route needs an account, and the server checks the account's role on each request. This page is the whole picture: who can do what, how sessions, tokens, second factors and trusted networks work, how Home Assistant and a wall screen sign in, what happens to a 4.x login, the audit log, and how to get back in when you are locked out. The short version of the trust model is in [A trusted network](/en/docs/trust/) and [Security](/en/docs/deep/security/).

If you lose the administrator's password, you do not need the web interface to get back in: `meshloom-admin` works on the machine that holds the database. Whoever can run it there already controls Meshloom's data, so it asks for no login. It changes the database file directly. A running Meshloom sees the change on its next request: no restart.

## First sign-in

The first time Meshloom opens, it asks for the administrator account (see [First launch](/en/docs/first-run/)). A Meshloom that already held data before it asked for accounts also asks for an [installation code](#the-installation-code), so nobody else on the network can claim it first. Under Home Assistant, the first Home Assistant user to open Meshloom becomes its administrator and nothing is asked ([Home Assistant](#home-assistant)). A kiosk screen has its own way in ([Kiosk screen](#kiosk-screen)).

## Roles

Each account has one of four roles. The server checks the role on every request, whatever the interface shows: a request outside the role is refused (`auth_forbidden`).

| Role | Can |
|---|---|
| `admin` | Everything: settings, radio, repeaters and rooms, integrations and bots, backup and restore, the private key, updates, Community, notifications, the debug report. |
| `member` | Everything a `viewer` can, plus send messages, add and edit contacts, channels and contact groups, favourites, pins and muted channels, subscribe its own browser to push notifications, and create API tokens for its own devices and scripts. |
| `viewer` | Read channels and direct messages, mark them read, manage its own profile and sessions. |
| `kiosk` | Read only, for a screen on the wall: channel messages (muted and pending channels included), contacts, channels, packets, statistics and the map. Never a direct message, no settings, no profile. |

What each role sees in the interface follows the table: the screens a role cannot use are hidden or greyed out instead of failing when clicked.

| | `admin` | `member` | `viewer` | `kiosk` |
|---|---|---|---|---|
| Channels and packets | yes | yes | yes | yes (channels only, no direct message) |
| Direct messages | yes | yes | yes | no |
| Composer, new conversation, pin, favourite, mute, delete | yes | yes | no | no |
| Repeater and room panes, mesh test | yes | no | no | no |
| Settings | all sections | Local configuration, Users & Access (own account), Extensions (read only) | Local configuration, Users & Access (own account) | none |
| Language and theme of this browser | yes | yes | yes | yes, from the account button |

Every role receives the instance's theme, its blocked contacts and names and the region names, whatever it can change: a blocked contact stays hidden for a viewer or a kiosk too. Only administrators read or change the other settings.

An action the server refuses anyway (for example from a page left open before a role change) shows one message, “This account is not allowed to do that.”

The first administrator, and the account Meshloom creates from the Basic login of a 4.x installation ([below](#the-4x-basic-login)), are `admin`: an installation with a single administrator works as before.

## Managing accounts

Administrators manage the accounts in **Settings → Users & Access**, or through the API. The page lists every account with its role and where it signs in (local or Home Assistant), and offers, per account: change the role, disable or enable, reset the password (the user is asked for a new one at the next sign-in unless you untick it), sign out everywhere, and delete. You cannot change, disable or delete your own account there, which keeps at least one administrator in place. A kiosk has no password. At the bottom, the role given to a Home Assistant user seen for the first time is set there too; the trusted proxies stay in **Local Configuration**.

Every user (the kiosk excepted) sees **My account** and **My sessions** on the same page: change the display name and password, see the devices signed in (browser, system, address, last seen), revoke one, or sign out everywhere else.

| Request | What it does |
|---|---|
| `GET /api/auth/users` | Lists every account with its role, its origin (`local`, or `ingress` for a Home Assistant user), whether it is disabled and whether it has a password. |
| `POST /api/auth/users` | Creates an account: `{"username", "role", "password", "display_name"}`. Every role but `kiosk` needs an initial password, which the person changes at their first sign-in (send `"must_change_password": false` to skip that). A `kiosk` has no password. |
| `PATCH /api/auth/users/<id>` | Changes the role, the display name, `disabled` (`true` blocks the account) or `must_change_password`. |
| `POST /api/auth/users/<id>/password` | Sets a new password, to change at the next sign-in unless `"must_change_password": false`. |
| `DELETE /api/auth/users/<id>/sessions` | Signs the account out everywhere. |
| `DELETE /api/auth/users/<id>` | Deletes the account. |

- **There is always an administrator.** The last active administrator cannot be demoted, disabled or deleted: the request is refused (`auth_last_admin`). Promote or enable another administrator first.
- **Changes apply at once.** A new role, a disable, a deletion or a password set by an administrator signs the account out everywhere, open pages included, and forgets the browsers it was remembered on. Moving an account to `kiosk` removes its password.
- **A password chosen by an administrator is temporary.** Until the person picks their own, Meshloom lets them do nothing else than change it (or sign out); the request is `PATCH /api/auth/me` with `{"current_password", "new_password"}`. Changing your own password signs your other browsers out. A wrong current password counts like a wrong sign-in, with the same delays (below).
- **Home Assistant users** (see the add-on below) take any role, `kiosk` included, and can be disabled, but never get a password: Home Assistant signs them in. The role given to the next new ones is `ingress_default_role` (`member` by default), set with `PATCH /api/auth/settings`. A deleted Home Assistant user comes back with that role on their next visit, even the one who was made administrator first (only the very first Home Assistant user ever becomes administrator): disable them instead to keep them out.

### When an administrator sets your password

If an administrator sets your password (or `meshloom-admin reset-password --must-change` does), Meshloom shows **Change your password** instead of the app: type the current password, then a new one (at least 12 characters, with the rules shown if it is refused). Nothing else works until it is changed, then the app opens where you were. Other devices of that account are signed out. A kiosk display has no password and never gets this page; if it ever shows, ask an administrator.

## Sessions

Signing in opens a **session**: Meshloom sets a cookie on that browser and keeps only a fingerprint of its secret. The sign-in form has a **Keep me signed in on this device** box. Checked, the cookie lasts 30 days and slides: each visit pushes the end back, so a browser you use is never signed out, and neither is a page that only keeps its live connection open, such as a wall screen. After 30 days without any visit, you sign in again. Unchecked (the default), the cookie disappears when you close the browser, and the session also ends after 12 hours without a visit, even if the browser stays open. The API takes the same choice as `remember` in `POST /api/auth/login` (`false` by default). A browser that supports passkeys, on an address where Meshloom does not offer them, says so under the form. Over HTTPS the cookie is `__Host-meshloom_session` (secure, host-only); over plain `http://` it is `meshloom_session`, which a browser sends in clear: use [HTTPS](/en/docs/deep/https/) beyond a network you trust.

- **My sessions** (**Settings → Users & Access**) lists the browsers signed in to your account, with system, address and last visit. **Revoke** signs one out, **Sign out everywhere else** all but this one. The live connection of a revoked session closes at once.
- **Sign out** revokes the session and clears the cookie.
- An account keeps at most 50 sessions: signing in once more signs out the oldest.
- A disabled or deleted account, a changed role or password, or `meshloom-admin revoke-sessions` ends its sessions; a page already open loses its live connection within 30 seconds at most.
- Other web sites cannot act with your session: the cookie is `SameSite=Lax`, and Meshloom refuses changes that come from another site. Under Home Assistant there is no Meshloom cookie at all (Home Assistant's own session is used).

## Sign-in delays

Wrong passwords slow the next attempts down; they never lock an account.

- **From one address, for one account:** the first 5 wrong passwords cost nothing. After that, the next attempt waits 1 second, then 2, 4, 8 and so on, up to 1 minute. Meanwhile the sign-in page says how many seconds to wait. Once the wait is over the right password works, and it clears the delay.
- **For one account, from anywhere:** after 5 wrong passwords, the account takes at most one attempt per second, whoever sends it. Someone flooding `admin` gets one try a second, and a browser Meshloom does not know competes with them for that second (see below).
- **From one address, for any account:** past 100 wrong passwords in 15 minutes, that address is refused until its count goes down (one more attempt every 9 seconds).

An IPv6 address counts by its /64 block: one home or server usually holds a whole block and could otherwise change address at each attempt.

**Your own browser always gets in.** After you sign in, Meshloom remembers the browser with a cookie of its own (for 180 days, for the last account that signed in on it). With that cookie, the right password gets in at once, whatever anyone else sends from anywhere. A browser that types the wrong password 5 times loses that pass and follows the rules above; its mistakes are forgotten after 15 minutes without a new one, or at its next successful sign-in. Changing the account's password, role or name, disabling it, restoring a database copy, or `meshloom-admin rotate-device-key` forgets the browsers: the next successful sign-in remembers it again. A restart or an update keeps them.

From a browser Meshloom does not know yet (a new device, a cleared cookie, after a key rotation):

- **On a Docker install (Compose or a plain container) with no trusted proxy set**, every visitor can arrive from Docker's own address, so the first and last rules are off and only the per-account rule applies. A guesser on the same address competes with you for the one attempt a second: one sending many attempts a second can keep you out until you use a browser Meshloom knows, or list your reverse proxy in **Trusted proxies**.
- **Behind a reverse proxy that is not listed in Trusted proxies**, every visitor counts as the proxy's address, so one guesser's delays apply to everyone behind it. List the proxy. Meshloom logs a warning naming it.
- **Otherwise**, each visitor has their own address and a guesser elsewhere only makes you wait up to a second.

The delays live in memory and start again empty when Meshloom restarts. A delay is written to the audit log once, not for each refused attempt.

## Behind a reverse proxy

Sign-in delays, the audit log and the session list record the address a request comes from. Behind a reverse proxy that is the proxy's, for every visitor, until you list the proxy in **Settings → Local Configuration → Trusted proxies**; Meshloom then uses the visitor's address the proxy forwards. List the proxy's own address only, never a Docker network or its gateway. See [HTTPS, trusted proxies](/en/docs/deep/https/#trusted-proxies). Meshloom also looks at the name in the browser's address: in 5.0.0 an unknown name (your proxy's DNS name, say) is only noted; from 5.1 it will be refused unless declared (**Settings → Users & Access → Public addresses**). See [Host names Meshloom answers to](/en/docs/deep/https/#host-names-meshloom-answers-to). A step-by-step setup for a tunnel, a VPN or a proxy: [Remote access on your own](/en/docs/deep/remote-self-hosted/).

## Trusted networks

An administrator can let devices on a chosen network in without a login, in **Settings → Users & Access → Trusted networks**: each rule is a network (`192.168.1.50`, `192.168.1.0/24`) and the account those devices act as. The list is empty by default. Use it for a device you control, such as a wall tablet, and give it the smallest network (its own address) and the weakest role that does the job (kiosk or viewer). A sign-in through a rule is written to the audit log once per device and per day.

A rule is **off as soon as a proxy is in the loop**, because behind a proxy or a tunnel every visitor, from anywhere, arrives from the proxy's address:

- a request that came through a reverse proxy or a tunnel (it carries `X-Forwarded-For`, `Forwarded`, `X-Real-IP`, `CF-Connecting-IP` or a similar header), or from an address listed in the trusted proxies, is never signed in by a rule;
- so are requests through Meshloom Remote and Home Assistant ingress (Home Assistant signs those in itself);
- a rule never covers this machine's own loopback (`127.0.0.1`, `::1`), except inside the Home Assistant add-on: a local proxy or an `ssh -L` tunnel would bring every visitor from there;
- the address in the browser must be an IP address, `localhost`, a local name (`.local`, `.home.arpa`, `.lan`, `.internal`), this machine's name or a public address you declared, so a web page cannot use a name of its own to reach Meshloom as that account.

Meshloom refuses to save a rule that overlaps a trusted proxy, one that covers loopback outside the add-on, one whose account must change its password first (the rule would never apply), and, while a public address is configured, one that covers the Docker gateway. Settings warns in red when a rule signs in an **administrator**: anyone on that network can then change every setting, account and plugin. It also warns as you type when a network is public or as wide as allowed, and flags a saved rule whose account has since been disabled or must change its password (the rule never applies). The **Access security notice** at the top of **Users & Access** says when a rule signs in an administrator while plugins run with reduced isolation (even while they are paused, since the risk comes back on resume), covers loopback or the Docker gateway while a public address is configured, or is a public network or one as wide as allowed (a `/8` IPv4 or `/16` IPv6 range), which is almost always a mistake.

## API and device tokens

A script, a Home Assistant sensor or a phone app talks to Meshloom with a **token** instead of a password: one per device, so you can revoke one without touching the others. Members and administrators create their own; a viewer or a kiosk cannot. The kiosk image's wall screen has one that Meshloom writes on the Pi itself (see [Raspberry Pi](/en/docs/rpi/)).

In **Settings → Users & Access → API and device tokens**, choose **Create a token**: give it a name, tick its scopes (only those your role allows are offered) and pick an expiry, or none. Meshloom then shows the token **once**, with a **Copy** button: it keeps only a fingerprint and cannot show it again, so copy it before you close the window. If you lose it, revoke it and create another. The list shows each token's scopes, creation, last use and expiry (an administrator also sees whose it is); **Revoke** asks for a confirmation, then the token stops working at once. Signed in through Home Assistant, the page tells you that tokens do not work through the add-on's ingress; you can still create tokens there for devices that reach Meshloom directly. The same actions are available through the API:

| Request | What it does |
|---|---|
| `POST /api/auth/tokens` | Creates a token: `{"name": "ha-sensor", "scopes": ["read"]}`, optionally `"expires_at"` (a Unix time). The answer holds the token (`mlt_…`) **once**: copy it then, Meshloom keeps only a fingerprint of it and cannot show it again. |
| `GET /api/auth/tokens` | Lists your tokens (an administrator sees everyone's): name, scopes, creation, last use, expiry. Never the token itself. |
| `DELETE /api/auth/tokens/<id>` | Revokes a token. It stops working at once, and a connection open with it is closed. |

The device sends the token in a header, `Authorization: Bearer mlt_…`, on every request and when it opens the live connection (`/api/ws`). A token put in the address (`?token=`) is ignored. It does not work through Home Assistant's ingress: point the device at Meshloom's own address.

**Scopes** say what a token may do: `read` (channels, contacts, packets, map), `read_private` (direct messages), `send` (send and edit contacts and channels), `repeater_admin` (repeaters and rooms) and `admin` (everything). A token never gets more than its account's role: a member cannot ask for `repeater_admin` or `admin` (`auth_token_scope`), and if an account loses a role later, its tokens lose it too. A disabled or deleted account takes its tokens with it. Give each token the fewest scopes it needs: a dashboard needs only `read`.

### Pairing a device

A phone app such as meshenger can get its token by scanning a QR code instead of copying one. In **Settings → Users & Access → Pair a device** (administrators only), choose **Pair a device**, name the device, tick the scopes it needs and pick how long the code stays valid (1 to 10 minutes). Meshloom then shows a QR code, drawn in your browser, with a countdown; scan it with meshenger (or another app that enrols with Meshloom) on the device. **Do not share this QR code and do not post a screenshot of it**: it is a one-time password for a new device, and whoever scans it before it expires gets access to your account with those scopes. When the countdown ends the QR code disappears and you can create a new one; **Cancel the code** stops it at once, and closing the window cancels a code that is still waiting. Once the device has used the code, the window says so. The panel lists the codes still waiting (name, scopes, who created them, expiry), each with a cancel button. The same steps through the API: an administrator creates a **pairing code** with `POST /api/auth/pairings`: `{"label": "Nadia's phone", "scopes": ["read", "send"]}`, optionally `"ttl"` (seconds, 30 to 600, 5 minutes by default). The answer holds the code (`mlp_…`) and the text of its QR code (`meshloom://pair?d=…`), **once**. The QR carries the code and the addresses where the device can reach Meshloom: your public addresses (**Settings → Users & Access → Public addresses**), then the local address you used to open Meshloom, only when it is a local network one (a private IP such as `192.168.x.x`, or a `.local` name; never `localhost` or a public address, which the app reaches only through your https public addresses). The app sends the code to `POST /api/auth/pair` with a name for the device and gets its own device token, with exactly the scopes you chose, attached to your account. It then appears in your list of tokens, where you revoke it like any other.

A code works **once** and only for its lifetime: a second try, an expired or a revoked code answer `auth_pairing_expired`, and you show a new one. `GET /api/auth/pairings` lists the pending codes, `DELETE /api/auth/pairings/<id>` revokes one at once. A code never gives more than your role allows, even if your role changed in the meantime. Wrong codes slow down the device or address that sends them, like wrong passwords, without slowing down anyone else. Pairing does not work through Home Assistant's ingress: the app must use Meshloom's own address. The technical contract for app developers is `docs/dev/device-pairing.md` in the Meshloom repository.

## Two-factor authentication

Any account with a password (the kiosk and Home Assistant users excepted) can add a second factor: a 6-digit code from an authenticator app (TOTP, RFC 6238: Aegis, 2FAS, Google Authenticator, a password manager…). Signing in then takes the password **and** the current code. It is optional; an administrator can require it for administrators (below). 

### In the interface

Go to **Settings → Users & Access → Two-factor authentication**.

1. **Set up.** Choose **Set up two-factor authentication** and confirm your password. Meshloom shows a QR code and the same key in text: scan the code with your authenticator app, or type the key. The QR code is drawn in your browser; the key never goes to another service. Enter the 6-digit code the app shows, then **Turn on**.
2. **Recovery codes.** Meshloom then shows ten recovery codes, **once**. Copy them, or download the file, and keep them somewhere safe (a password manager). Each works once if you lose your app. Tick that you saved them to close the list. Your other browsers are signed out.
3. **Signing in.** After the password, a second screen asks for the code from the app. **Use a recovery code** switches to a recovery code. A wrong code is explained and you can try again (too many attempts slow you down). If you wait too long (5 minutes), or five codes are wrong, Meshloom takes you back to the password.
4. **New recovery codes, turning it off.** Both ask for your password and a current code (turning it off also accepts a recovery code). New recovery codes replace all the old ones.
5. **Require it for administrators.** Administrators see **Require two-factor authentication for administrators** in the same section. Turn on your own second factor first: an administrator who has not set it up sees only the setup page until it is done.

Signed in through Home Assistant, or with an account that has no Meshloom password (a kiosk), the section says that two-factor authentication does not apply: Home Assistant already authenticated you. The API calls below do the same things.

| Request | What it does |
|---|---|
| `POST /api/auth/2fa/enrol` | Starts the setup: `{"password"}`. The answer holds the secret and an `otpauth://` link to scan or paste into the app, **this one time**: no other request shows the secret again. |
| `POST /api/auth/2fa/confirm` | Turns it on with the first code the app shows: `{"code": "123456"}`. The answer holds **ten recovery codes**, shown once: print them or keep them in a password manager. Your other browsers are signed out. |
| `GET /api/auth/2fa` | Whether it is on and how many recovery codes are left. |
| `POST /api/auth/2fa/recovery` | Ten new recovery codes (the old ones stop working): `{"password", "code"}` with a code from the app. |
| `POST /api/auth/2fa/disable` | Turns it off: `{"password", "code"}`, a code from the app or a recovery code. |

- **Signing in.** After a correct password, `POST /api/auth/login` answers `"authenticated": false` with a `second_factor.pending_token`; send it with the code to `POST /api/auth/login/2fa` within 5 minutes to get the session. A recovery code works in place of the app's code, once each. Five wrong codes, or a change to the account, mean starting again with the password.
- **A code works once.** A code already used, or an older one, is refused even while it is still shown in the app: wait for the next one.
- **Wrong codes count like wrong passwords**, with the same delays (above).
- **Scripts.** `Authorization: Basic` ([the 4.x window](#the-4x-basic-login)) stops working for an account with a second factor: use an [API token](#api-and-device-tokens).
- **Require it for administrators.** `PATCH /api/auth/settings` with `{"require_2fa_for_admins": true}` (off by default). An administrator without a second factor can then only set one up (and sign out) until it is done; members, viewers, the kiosk and Home Assistant users are not affected. Recommended when Meshloom is reachable from the Internet.
- **Lost phone.** Use a recovery code. Without one, an administrator runs `meshloom-admin disable-2fa <user>` on the machine (below), then the user signs in with the password alone and sets it up again.

The secret is kept in the database (encrypted at rest in a later version). The full backup carries it, with the recovery codes (only their hashes), only when **Include accounts** is checked; the database download leaves both out.

## Passkeys

A passkey signs you in with your phone, your computer or a security key (fingerprint, face or PIN on the device) instead of typing your password. It is optional, and your password keeps working.

Passkeys need a secure connection with a name: Meshloom offers them only on **https** through an address declared in **Settings → Users & Access → Public addresses** (behind a reverse proxy, list it in **Trusted proxies** too so Meshloom sees the https: [Behind a reverse proxy](#behind-a-reverse-proxy)), through Meshloom Remote, or on `http://localhost`. Over plain `http://` on the local network (an IP address or a `.local` name), and through Home Assistant, they are not available: browsers refuse them there. A passkey works only on the address it was created on.

### In the interface

- **Add one.** Go to **Settings → Users & Access → Passkeys** and choose **Add a passkey**. Give it a name (for example *Laptop* or *Phone*), enter your password and, if two-factor authentication is on, a code from your authenticator app or a recovery code, then follow your browser's prompt (fingerprint, face, PIN or security key). The section lists each passkey with its name, when it was added and when it was last used. A recovery code is used up as soon as you submit the form, even if you then cancel the browser's prompt: you would need another one to try again.
- **Sign in with one.** When the address supports passkeys and your browser does too, the sign-in page shows **Sign in with a passkey** under the password form. On a plain `http://` address the button is hidden and the page says why.
- **As a second factor.** After the password, an account that has two-factor authentication on and at least one passkey sees **Use a passkey** next to the code field.
- **Remove one.** Choose **Remove** next to it and enter your password. Your password keeps working.
- If you cancel the browser's prompt, let it time out, or try to add a device that already has a passkey for your account, Meshloom says so and nothing changes. The section is not shown under Home Assistant, nor for accounts without a password, and says why.

### API

The same things through the API (accounts with a password, not the kiosk or Home Assistant users):

| Request | What it does |
|---|---|
| `POST /api/auth/webauthn/register/options` | `{"password"}`, plus `"code"` (from the app, or a recovery code) when two-factor authentication is on: the options to pass to `navigator.credentials.create()`. |
| `POST /api/auth/webauthn/register/verify` | `{"credential", "name"}`: the browser's answer and a name for the passkey. Valid 5 minutes, once, from the same session. |
| `GET /api/auth/webauthn/credentials` | Your passkeys: name, creation, last use. |
| `DELETE /api/auth/webauthn/credentials/<id>` | `{"password"}`: removes one. Your password still signs you in. |
| `POST /api/auth/webauthn/login/options` | `{}`: the options for `navigator.credentials.get()` to sign in without a password (the device must check it is you). |
| `POST /api/auth/webauthn/login/verify` | `{"credential", "remember"}`: the browser’s answer (and "Keep me signed in"); opens the session. |

- **As a second factor.** With [two-factor authentication](#two-factor-authentication) on, a passkey can replace the code after the password: `POST /api/auth/login` lists `passkey` in `second_factor.methods`, `POST /api/auth/webauthn/login/options` with `{"pending_token"}` gives the options, and `POST /api/auth/login/2fa` takes `{"pending_token", "passkey"}`. Without two-factor authentication, a passkey changes nothing to signing in with the password.
- **A passkey replaces both the password and the code.** Signing in with a passkey alone is enough, even with two-factor authentication on: the device checks it is you (fingerprint, face or PIN), which counts as both factors. That is why adding a passkey asks for your password **and** a code when two-factor authentication is on. If you think someone used your account, review your passkeys and remove the ones you do not recognise, then change your password.
- **Failures count like wrong passwords**, with the same delays. Each challenge works once and expires after 5 minutes. A passkey whose counter goes back (a sign of a copied security key) is refused.
- **Lost device.** Sign in with your password and remove its passkey. An administrator can remove all of a user's passkeys on the machine with `meshloom-admin remove-passkeys <user>` (below).

Meshloom stores only the public part of each passkey. The full backup carries them only when **Include accounts** is checked; the database download keeps them.

## Home Assistant

On the Home Assistant add-on, Home Assistant signs you in. Opened from the sidebar, Meshloom accepts the Home Assistant user who opens it, with no password and no Meshloom cookie. Meshloom trusts the identity only when the request comes from the Home Assistant Supervisor itself; the same headers sent to a published port 8000 are ignored, and that address asks for a Meshloom account like any other install.

- The first Home Assistant user ever to open Meshloom becomes its administrator, even if a Meshloom administrator already exists, so the sidebar always reaches an administrator. The next ones get `ingress_default_role` (`member` by default, changed in **Users & Access**). Only Home Assistant administrators see the panel, so these are people who already administer your Home Assistant.
- One Meshloom account per Home Assistant user; its display name follows Home Assistant's. Such an account has no password, no second factor and no passkey. You change its role, disable it or delete it in **Users & Access** (a deleted one comes back at its next visit: disable it instead).
- Home Assistant and its add-ons trust each other: an add-on you would not trust with your Home Assistant should not run next to Meshloom.
- Through the sidebar, trusted networks, passkeys and API tokens do not apply (see each section). To reach Meshloom from a phone app or a script, give `8000/tcp` a host port in the add-on's **Network** panel; that address asks for a Meshloom account. If a reverse proxy or tunnel serves it under a name, declare the name in **Public addresses** ([HTTPS](/en/docs/deep/https/#host-names-meshloom-answers-to)).

## Kiosk screen

The `kiosk` role is for a screen on a wall (a club, a workshop): it reads channels, contacts, packets, statistics and the map, never a direct message, and has no password and no settings. Two ways to use it:

- **The Raspberry Pi kiosk image** creates the `kiosk` account itself and signs the screen in with a **device token** it writes on the Pi, readable only by the desktop user, working only from the Pi itself and never shown in an address (see [Raspberry Pi](/en/docs/rpi/#kiosk-image-screen-on-the-wall)). An administrator sees it as **Kiosk screen** in **API and device tokens**; **Revoke** replaces it, and disabling the `kiosk` account keeps the screen signed out.
- **Any other screen**: create a `kiosk` account in **Users & Access**, then add a trusted-network rule that maps the screen's own address to it ([below](#trusted-networks)). A `kiosk` account has no password, so it cannot sign in any other way.

## The 4.x Basic login

Up to 4.18, `MESHCORE_BASIC_AUTH_USERNAME` and `MESHCORE_BASIC_AUTH_PASSWORD` (the add-on's `basic_auth_username` and `basic_auth_password`) were one shared login. From 5.0:

- At the **first start with no account**, when both are set, they become the administrator account (same name, same password, no installation code needed). Meshloom never reads them again; it logs a warning at every start while they are set: remove them. Only one set imports nothing.
- **Compatibility window.** Until 5.2, scripts that send that user name and password with `Authorization: Basic` keep working, for any account that has a password and no second factor, with a short pause after a failure. It logs a deprecation warning. In 5.2 the header is removed: move scripts to an [API token](#api-and-device-tokens) now.
- A 4.x user name longer than 64 characters cannot become an account: the import is skipped, with an error in the log, and the normal first sign-in applies.

## Audit log

Meshloom keeps a log of sensitive actions: sign-ins (successful or failed), sign-outs and sessions signed out, accounts created, renamed, given another role, disabled, enabled again or deleted (refusals included, such as an attempt to demote the last administrator), password changes, two-factor authentication turned on or off, new recovery codes and sign-ins waiting for their code, passkeys added or removed, the first administrator setup, changes to the access settings, tokens created and revoked and uses of a revoked or expired token, bots created, changed, deleted or stopped, backup restores, private key imports, radio factory resets and updates started. Each `meshloom-admin` command below writes an entry too, refused ones included.

Each entry holds the time, the account that acted (none for a failed sign-in or the command line), the address it came from, the way in (local network, Home Assistant, command line, or Meshloom itself for an automatic update), what was acted on and how it ended. It never holds a password, an installation code, a session, a key or a bot's code. A failed sign-in names the account only when the typed name matches one; any other typed name is recorded as unknown, so a password typed into the name field is not kept.

Administrators read it in **Settings → Users & Access → Audit log** (hidden from everyone else), or through `GET /api/auth/audit`: newest first, by pages, with a button to load older entries. The filters (action family, action, outcome, account, dates) narrow the entries already loaded; load older ones to look further back. Entries are kept **366 days**, then deleted by an hourly clean-up; an administrator changes that with `PATCH /api/auth/settings` and `{"audit_retention_days": <1 to 36500>}`. The log is not in the JSON backups; the database download (**Settings → Database**) keeps it.

## Backup and restore

The full backup (**Settings → Database**) carries the accounts when **Include accounts** is checked, which it is by default: their roles, password hashes, two-factor secrets and passkeys, and the API tokens (only their fingerprints, so devices keep working after a restore), never the sessions or the audit log. Restoring it on a new machine brings the same accounts and passwords back. A restore replaces the accounts and signs everyone out; it never leaves Meshloom without an enabled administrator who can sign in here (a password account, or a Home Assistant user on the add-on): a file without one, or without accounts at all, keeps the current accounts. Details on the [backup page](/en/docs/backup/#what-the-backup-contains). The database download keeps the accounts but not their passwords: after putting it back, reset a password with `meshloom-admin` (below).

## Commands

| Command | What it does |
|---|---|
| `meshloom-admin reset-password <user>` | Sets a new password (asked twice, or one line on standard input with `--stdin`) and signs the user out everywhere. `--must-change` asks for another at the next sign-in. Refused for a `kiosk` or Home Assistant account, which has no password. |
| `meshloom-admin create-admin <user>` | Creates an administrator. Same password options, plus `--display-name`. |
| `meshloom-admin setup-code` | Issues a new installation code and prints it (see below). |
| `meshloom-admin list-users` | Lists the accounts: id, role, provider, active or disabled, whether a password is set. `--json` for scripts. |
| `meshloom-admin disable-user <user>` | Blocks a user and signs it out. Refuses the last active administrator unless you add `--force`. |
| `meshloom-admin enable-user <user>` | Lets a disabled user sign in again. |
| `meshloom-admin disable-2fa <user>` | Removes a user's two-factor authentication and recovery codes (lost phone). The user then signs in with the password alone. |
| `meshloom-admin remove-passkeys <user>` | Removes all of a user's passkeys (lost or stolen device). The password keeps working. |
| `meshloom-admin revoke-sessions <user>` or `--all` | Signs one user, or everyone, out. |
| `meshloom-admin host-guard-observe` | Switches the [Host check](/en/docs/deep/https/#host-names-meshloom-answers-to) back to observation mode, for when strict mode refuses the only name you can reach Meshloom by. |
| `meshloom-admin rotate-device-key` | Forgets every remembered browser (see [Your own browser always gets in](#sign-in-delays)): each one follows the ordinary delays until its next successful sign-in. |

A running Meshloom sees these changes on its next request; a browser that stays open on a page signed out this way loses its live connection within 30 seconds.

Passwords are at least 12 characters and are never taken from the command line, so they stay out of your shell history. `meshloom-admin --help` and `meshloom-admin <command> --help` list every option. A command exits with 0 when it worked and 1 with a message when it did not, for example when the database file is missing, unreadable or not yet upgraded (start Meshloom once on the new version first).

### The installation code

When Meshloom is updated from an older version, the first administrator must prove they have access to the machine by typing an installation code. Meshloom stores only a fingerprint of the code, so it cannot be shown again: `meshloom-admin setup-code` makes a **new** one and the previous code stops working. The command only works while no account exists; afterwards use `create-admin` or `reset-password`.

The code has 8 digits; Meshloom also prints a new one in its log at each start while no account exists. Spaces or dashes typed inside it are ignored. A fresh installation asks for no code. If Meshloom could not prepare the first start (the log says so), creating the administrator is refused until it can; restart Meshloom if it lasts. A code printed by `meshloom-admin setup-code` in the meantime stays valid: Meshloom does not replace it when it tries again.

## The command for each installation

Replace `alice` with the account name.

### Linux package (apt or dnf)

Run it with `sudo`:

```bash
sudo meshloom-admin reset-password alice
```

The command refuses to run without root. As root it reads the database path from `/etc/meshloom/meshloom.env` (a file only root can change), then switches to the `meshloom` user, which owns the database, so SQLite never leaves files the service cannot write. The same command is `/opt/meshloom/.venv/bin/meshloom-admin`.

### Docker and Docker Compose

Run it inside the container, from the folder holding `docker-compose.yml`:

```bash
docker compose exec meshloom meshloom-admin reset-password alice
```

With `docker run`, use `docker exec -it <container> meshloom-admin reset-password alice`. If the container runs as user 10001 (`MESHLOOM_RUN_AS_USER`), add `--user 10001` to `exec`, so the database files keep their owner. To feed a script, read the password without echoing it and pass it on standard input, so it never appears in your shell history: `read -rs pw; printf '%s\n' "$pw" | docker compose exec -T meshloom meshloom-admin reset-password alice --stdin`.

### Home Assistant add-on

Through the sidebar, Home Assistant signs you in: Meshloom accepts the Home Assistant user who opens it, with no password of its own. The first Home Assistant user to open Meshloom becomes its administrator, even if a Meshloom administrator already exists (from the 4.18 `basic_auth_*` options or `meshloom-admin`), so the sidebar always reaches an administrator; the next ones get the **Member** role (an administrator can change it, and each user's role: see "Managing accounts" above). Only Home Assistant administrators see the Meshloom panel, so these are people who already administer your Home Assistant. If you cannot open Meshloom, fix your access in Home Assistant itself. If you gave the add-on's port 8000 a host port, that address asks for a Meshloom account like any other install. The command still exists for the rare case where you need it. It runs in the add-on's container, so it needs a way to reach the host's Docker: the *Advanced SSH & Web Terminal* add-on with **Protection mode** turned off.

```bash
docker ps --format '{{.Names}}' | grep meshloom
docker exec -it addon_xxxxxxxx_meshloom meshloom-admin list-users
```

Use the name printed by the first command. The add-on's database is `/config/meshcore.db` inside the container, which the command finds by itself.

### Raspberry Pi image

The image installs the Linux package, so the command is the same. Open a shell on the Pi over SSH (the user and key you set in Raspberry Pi Imager), then:

```bash
sudo meshloom-admin reset-password alice
```

If you need the installation code and cannot see the log, `meshloom-admin setup-code` gives a new one over SSH.

### Copy of the code

From the Meshloom folder, as the user that runs it:

```bash
cd ~/meshloom
uv run python -m app.cli reset-password alice
```

The database is `data/meshcore.db` in that folder.

## If it does not start

- **`database not found`**: the command looked at the wrong file. Give it the path with `--db /path/to/meshcore.db`, or set `MESHCORE_DATABASE_PATH`.
- **`run it as root`** (package): put `sudo` in front.
- **`cannot read /etc/meshloom/meshloom.env`**: pass the database before the command: `sudo meshloom-admin --db /path/to/meshcore.db reset-password alice`. The file is then not read.
- **`refusing /etc/meshloom/meshloom.env`**: the file, or its folder, is not owned by root, can be changed by another user, or is a link, so the command does not trust the database path in it. Reinstalling the package restores the folder (`root:meshloom`, `0750`); fix the file with `sudo chown root:root /etc/meshloom/meshloom.env` and `sudo chmod 0640 /etc/meshloom/meshloom.env`. Meanwhile, `--db` works as above.
- **`cannot read database` or `cannot open database for writing`**: you are not the user that owns the file. Use `sudo meshloom-admin` (package) or `docker compose exec` (Docker).
- **`start Meshloom once`**: the database is older than the program. Start Meshloom so it upgrades the database, then run the command again.
