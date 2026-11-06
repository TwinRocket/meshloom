# Meshloom Remote on the instance

Meshloom Remote lets a user reach their instance from outside the LAN through
`https://<slug>.meshloom.link`, without opening a port. It is a **blind relay**: no
TwinRocket account, no portal, no pairing (owner decisions of 2026-10-27). The instance
registers an Ed25519 key, gets a slug, and decides alone who may get past the relay's
edge lock, by signing **device passes**.

Contracts (the source of truth, with their vectors): `meshloom-community`
`docs/contracts/remote-registration.md` (registry, instance JWT) and
`docs/contracts/remote-relay.md` (frames, HELLO, CONTROL ops, edge lock, passes). The
vectors are copied byte for byte in `tests/fixtures/remote_frames_vectors.json`, pinned
by sha256 in `tests/test_remote_identity.py`; refresh the copy in a `CONTRACTS:` commit.

This page covers identity, registration, passes, enrolment, access requests, revocation
and the control API (MSHL-79), then the relay client: the outbound socket, HELLO and the
in-process dispatch of relayed requests (MSHL-87), and the settings UI (MSHL-82) built on
both.

## Code map

| File | Role |
|---|---|
| `app/services/instance_identity.py` | The `remote` instance key (`app_settings.remote_instance_seed`), `mint_remote_jwt()`, `sign_with_instance_key()`. The only identity module |
| `app/services/remote_passes.py` | Pass format, the contract verifier `verify_pass`, access code normalisation. Pure functions |
| `app/services/remote.py` | Opt-in, registry client, state changes, devices, enrolment, access requests, CONTROL ops, the relay link interface, the `remote` on_restore hook |
| `app/services/remote_frames.py` | The relay frame codec (contract "Frames"), limits and their clamping. Pure functions |
| `app/services/remote_relay.py` | The relay client: connect loop and close-code policy, HELLO, stream table, credits, batching, in-process ASGI dispatch |
| `app/repository/remote.py` | `remote_state`, `remote_devices`, `remote_enroll_tokens` (migration 138, `docs/dev/database.md`) |
| `app/services/community_attestation.py` | The Community attestation of the Remote key (audience `remote`), shared with Links (`docs/dev/community-client.md`, "Attestations") |
| `app/routers/remote.py` | `/api/remote` |

## Identity

- The Remote key is its own random seed, never the radio key and unrelated to the share
  key (`app/AGENTS.md`, "Instance identities"). Its fingerprint (first 8 bytes of
  SHA-256 of the public key, `XXXX XXXX XXXX XXXX`) is `key_fingerprint` in
  `GET /api/remote`.
- The instance JWT is the Community wire format with `aud` `relay.meshloom.link`, a
  300 s lifetime and a required `jti`: one per registry request, one per relay connect.
- The slug belongs to the key: a new key means a new slug and every device enrols again.

## Opt-in and egress

`remote_state.enabled` is off by default. While it is off, nothing leaves towards
`relay.meshloom.link`: every registry request goes through `remote._registry()`, which
checks the opt-in first (409 `remote_disabled`, no connection), then sends through the
egress guard with `RELAY_HOST` as the only host, HTTPS only, no redirect, the answer
capped at 16 KiB. `tests/test_remote_off_no_egress.py` sweeps it.

The relay socket follows the same rule: `remote_relay` connects only while Remote is on
**and** registered, to `wss://<RELAY_HOST>/v1/connect` and nowhere else (never a host read from storage: the
registered `relay_url` is informative), without any system proxy.
Remote needs Community on (MSHL-445: every feature backed by TwinRocket infrastructure
does). `remote.community_on()` reads the stored opt-in (`meshloom_community.community_enabled()`,
waiting for a position counts as on). While Community is off, `enable()` and every
`_registry()` call answer 409 `remote_community_off` with no connection and nothing
written, and the relay client does not connect (`link_status` `community_off`).
Turning Community off while Remote is on **pauses** it, the least destructive choice:
`remote_state` (opt-in, slug, devices, gate) is kept, the relay loop is stopped (socket
closed), the pending access requests are dropped; turning Community on again restarts
the loop. The hook is `meshloom_community.add_enabled_change_listener`, called by
`_update_community_locked` once the opt-in flipped (after the opt-out teardown); `remote`
registers `_on_community_change`, which runs the Remote change listeners. With Community
on and Remote off nothing reaches the relay (`tests/test_remote_off_no_egress.py`); with
Community off and Remote on nothing does either (`tests/test_community_off_no_egress.py`,
`TestRemoteNeedsCommunity`, and `tests/test_community_required.py`).

## Attestation

Since MSHL-445 the relay registers a key only with a Community attestation of it
(`remote-registration.md` § *Attestation*). `_register_locked` (enable, `register` after a
close `4003`, rotate) asks `community_attestation.attestation_for("remote")` and sends it as
`X-Meshloom-Attestation` on `POST /v1/instances`; no other registry route and not the relay
socket carry it.

- No attestation (Community refused, a hold, unreachable): the registration is sent
  without the header. The relay lets a key registered before 2026-11-10 register without
  one until 2027-01-31 (grace), so an instance already registered keeps registering while
  Community cannot vouch for it. If the relay asks for one anyway (403 `attestation_*`),
  the Community reason is the error: `remote_radio_not_heard`, `remote_community_opted_out`,
  `remote_community_banned`, `remote_attestation_unavailable`.
- Refused attestation (403 `attestation_missing`, `attestation_invalid`, `attestation_expired`):
  `renew_after_rejection` once, the registration once more. Refused again, an instance already
  registered (a slug) tries one last time without the header (the relay verifies any header
  it gets, even from a grace key); else `remote_attestation_unavailable`. Three requests at
  most, never a loop.
- The error code goes to `last_error`; the settings show the translated sentence of a
  `remote_*` code (`errors.<code>`) instead of the raw code.
- `503 registration_paused` (the relay has no trusted attestation key yet, or its kill
  switch) stays `remote_registry_error` with `registry_code` `registration_paused`.

## State changes

| Call | Route | Effect |
|---|---|---|
| `enable` | `POST /api/remote/enable` | 409 `remote_community_off` while Community is off (nothing written). Otherwise turns Remote on and registers (`POST /v1/instances` with `version`, `install_kind`, `plugins: {enabled, isolation}` and the attestation, above) when not registered. Stores `slug`, `hostname`, `relay_url` and declares the hostname to the host guard. A registry failure keeps Remote on, unregistered, with `last_error`; calling it again retries |
| `register` | (relay client) | The same registration, idempotent: after a close `4003` |
| `disable` | `POST /api/remote/disable` | Turns Remote off and withdraws the hostname; registration and devices are kept |
| `unregister` | `DELETE /api/remote` | `DELETE /v1/instances/self` (a 404 `instance_unknown` counts as done), forgets slug and hostname, turns Remote off, **keeps the key** (the registry holds the slug 30 days for it). 409 `remote_disabled` while off |
| `rotate` | `POST /api/remote/rotate` | Removes the alert e-mail, then deletes the old registration, while the old key still signs (best effort: a refusal such as a pause leaves the registration on the relay), replaces the key, forgets every device, enrolment token and access request, resets the generation, and registers again when Remote is on (a new slug) |
| on_restore `remote` | any restore | The `instance_identity` hook replaced the key; this one forgets registration, devices and tokens and turns Remote off. A database copy also empties the three tables (`SNAPSHOT_EMPTIED_TABLES`) |

`GET /api/remote` returns `state`: `disabled`, `unregistered` (on, no slug) or
`registered`, plus `relay_connected` and `link_status` as the relay client reports them,
and `community_enabled` (the Community opt-in Remote needs).

## Device passes

Format and rules: `remote-relay.md`, "Passes". The instance signs:

| Kind | Lifetime | Issued by |
|---|---|---|
| `enroll` | 15 min | `POST /api/remote/enroll` (QR code) |
| `browser` | 90 days, renewed while used | a QR enrolment or an approved access request (the relay sets `__Host-mlgate`), then a renewal |
| `client` | 365 days | `POST /api/remote/devices/client` (the `X-Meshloom-Pass` header of a script or meshenger), shown once |

`iss` is the slug, `sub` the device id (22 base64url characters), `uid` the Meshloom user
id the device belongs to, `gen` the current generation. Every device is a row of
`remote_devices`.

`verify_device_pass(token, slot)` is the third check of the edge lock, for the relay
client to run on `x-meshloom-pass`: the contract verdict, then the device must exist,
not be revoked, belong to the pass's `uid`, and that account must still exist.

### Last seen

`remote_devices.last_seen_at` is the time of the device's last relayed use, shown in the
devices list. `relayed_scope` calls `remote.note_device_seen` once the pass is accepted, for a
request or at the opening of a WebSocket (not for its frames), on the login page and static
files too: a device is seen when its pass is used, signed in or not. At most one write per
device every 5 minutes (`DEVICE_SEEN_INTERVAL_S`, kept in memory, taken before any `await`, so
a page's burst writes once; a restart writes once more). The write runs in the background
(`spawn`): a failure only logs its type, and neither it nor a slow database reaches the
request. Nothing is written while Remote is off, as there is no relay link to carry a request.
The edge lock, the revoked list and the renewal never read this field.

### Renewal of a browser pass

The relay client adds `x-meshloom-set-pass: <new pass>` to a `RESP_HEAD`
(`HttpStream._renewed_pass`, `remote.renew_browser_pass`); the relay checks it (cookie slot,
same `sub`), sets `__Host-mlgate` and removes the header. The relay's side is contract
"Renewal"; the rest is the instance's choice:

- **When.** A 2xx answer to a request under `/api/` with a browser pass and a signed-in
  principal, of any account: a device approved through an access request belongs to its
  approver, not to whoever signs in on it. A device left on the login page, a failed request
  and a static file renew nothing. The answer to the login itself does not (nobody was signed
  in yet); the next API call does.
- **How often.** A pass under one day old (`BROWSER_PASS_RENEW_AFTER_S`) costs one JSON decode
  and nothing else. Past that, one attempt per device every 10 minutes
  (`BROWSER_PASS_RENEW_RETRY_S`, kept in memory, set before the first `await`), so the
  concurrent requests of a page renew once and an answer that never reached the browser is
  retried without a surge. The relay only verifies a signature: no KV or D1 write, no stored
  state.
- **Revocation wins.** The pass is verified again when the answer is ready (the request may
  have revoked its own device), a pass past its `exp` in the 60 s of clock skew is refused,
  and `extend_device_pass` updates `pass_expires_at` only on a row not revoked, before the pass
  is handed out. The revoked list thus keeps a device as long as its last pass lives. `gen` is
  the old pass's, so a new generation also refuses it.
- **Failure.** A failed renewal only logs the exception type and the request carries on. A
  head within about 2 KiB of the 64 KiB limit renews nothing.
- **Containment.** `outbound_headers` drops the header name from anything the app sends, on
  HTTP and on WebSockets. The pass is held by the stream, not in the ASGI scope. A WebSocket
  never renews: the relay reads the header on HTTP answers only, and a page's API calls do it.
- **Limits.** An older pass stays valid until its own `exp`, and renews too: a copied pass ends
  only by revoking the device. A renewal in flight while the browser posts
  `/_meshloom/forget` can set the cookie again; a later forget or a revocation ends it.

## Enrolment by QR code

1. A signed-in user asks `POST /api/remote/enroll` from the LAN (refused through the
   relay: 403 `remote_lan_only`). The answer is
   `https://<hostname>/_meshloom/enroll#token=<enrol token>` and its expiry; the UI
   shows it as a QR code. The token is single use (`remote_enroll_tokens`).
2. The new device opens the URL; the relay checks the token and sends CONTROL `enroll`.
3. `handle_control` → `answer_enroll` checks it again (contract verifier, a `jti` of
   ours, unused, same generation, the user still enabled), creates a `browser`
   device for the token's user and answers `pass` with its pass, within 8 s (the
   contract allows 10). Otherwise `error`: `enroll_unknown` (not one of our tokens)
   or `enroll_refused`.

## Access requests

1. The relay sends CONTROL `access_request` (`request_id`, `code`, `user_agent`,
   `created_at`). The instance keeps it in memory for 10 min, at most 5 (the oldest
   goes first); `GET /api/remote/access-requests` lists them.
2. A signed-in user (on the LAN, or on an enrolled device) types the code the new device
   shows: `POST /api/remote/access-requests/approve` matches it after normalisation
   (`normalize_access_code`), creates a `browser` device for **the approver** and sends
   `access_grant` with its pass. Without a relay link: 503 `remote_offline`, nothing is
   created. `POST /api/remote/access-requests/{id}/deny` sends `access_deny`.

## Revocation and the gate state

- `DELETE /api/remote/devices/{id}` (one's own; an administrator any) marks the device
  revoked. The revoked list of the generation holds the devices whose pass may still be
  presented (until `pass_expires_at` + 60 s, which follows the renewals). Past 1024 ids (the relay's limit), the
  generation is bumped instead.
- `DELETE /api/auth/users/{id}` revokes the account's devices the same way
  (`revoke_account_devices`), and a device approved through an access request goes with its
  approver, even if someone else signs in on it. Deleting that other person's account does
  not touch it. `verify_device_pass` refuses a device whose account is gone anyway, which
  covers a row left by a deletion before this rule and any other deletion path.
- `POST /api/remote/devices/revoke-all` (admin) bumps `gate_gen`: every pass dies.
- `gate_state()` is `{gen, revoked}`. Each change is pushed at once as CONTROL `gate`
  when a relay link is up (`gate_pushed` in the answer); otherwise the next HELLO
  carries it.

## Relay link interface

What `remote.py` offers the relay client (`remote_relay.py`, below):

- `set_relay_link(sender, status)`: the async callable that sends one CONTROL op
  (returns True when sent), or None when disconnected; `set_link_status(status)` for
  the UI states of the close codes (`replaced`, `banned`, `update_required`...).
- `handle_control(frame)`: handles `enroll` (returns the `pass` reply to send),
  `access_request` and `status` (a new name from the operator); other ops are ignored.
- `instance_info()`, `plugins_info()` and `gate_state()` fill the HELLO.
- `register(actor)` after a `4003`; `add_change_listener(callback)` to connect or
  disconnect when Remote is enabled, disabled, registered, rotated, unregistered or
  reset by a restore (adding the same callback twice is a no-op).
- Web Push also listens (`app/push/vapid.py`, MSHL-446): with no usable typed VAPID subject,
  `https://<hostname>` is the subject while Remote is on and registered; derived, never stored.
- `verify_device_pass(token, slot)` on every relayed request.

## Relay client (`remote_relay.py`)

Started by the lifespan (`remote_relay.start(app)`) and stopped at shutdown. One loop,
one socket at most; every Remote change restarts it. Restarts and stops are serialized
by a lock held from stopping the old loop to creating the new one, so overlapping
changes never leave a second loop running.

**Connect.** A fresh instance JWT per attempt (`Authorization: Bearer`),
`X-Meshloom-Relay-Protocol: 1`, `User-Agent: Meshloom/<version>`, no compression,
messages up to 1 MiB. Liveness is the `websockets` RFC 6455 ping: every 30 s, dropped
after 60 s without a pong (two missed pongs). There is no application ping, and the
text `ping` is never sent on this socket.

**HELLO.** The instance sends first: `instance` (`version`, `install_kind`), `plugins:
{enabled, isolation}` (`remote.plugins_info()`, isolation `null` when unknown; the
legacy `bots_enabled` is never sent), `gate` (`{gen, revoked}`), the limits it wants
(the defaults) and `features: []`. The relay must answer its HELLO within 10 s
(`1002 hello_timeout` otherwise; an invalid one is `1002 hello_invalid`). Its limits are
clamped to the contract and used from then on; its `instance.slug`/`hostname` are
applied like a CONTROL `status`. Only then is the CONTROL sender installed
(`relay_connected` true, `link_status` `connected`).

**Dispatch.** Each `REQ_HEAD` or `WS_OPEN` (odd streams, at most `max_streams`) runs
the ASGI app in process, `await app(scope, receive, send)`, never over a local socket:

- the head must name the current Remote hostname (else 421 `remote_host_mismatch`,
  never dispatched), the `client_ip` must be an IP address and the path and query
  ASCII (else 400 `remote_request_invalid`);
- `x-meshloom-pass` must be one valid `browser` or `client` pass of a known,
  unrevoked device of its `uid` (`verify_device_pass`; the slot follows the kind),
  else 401 `remote_pass_required` (a WebSocket gets `WS_ACCEPT` 401). A relay bug or
  compromise alone therefore never reaches the app;
- the scope is `scheme` `https` (`wss` for a WebSocket), `server` `(<hostname>, 443)`,
  `client` `(client_ip, 0)`, `state` `{"via": "relay", "remote_device": {id, uid,
  kind}}`, `path` percent-decoded and `raw_path` as sent. The headers start with
  `host: <hostname>`, then the relay's pairs minus the hop-by-hop ones (and any name
  `connection` lists), `proxy-*`, `x-forwarded-*`, `forwarded`, `x-real-ip`,
  `true-client-ip`, `cf-*`, `cdn-loop`, `x-ingress-path`, `x-remote-user-*`,
  `sec-websocket-*` and the `x-meshloom-*` relay headers.

So a relayed request is never LAN (`request_via()` is `relay`: no trusted network, no
login-free LAN, no ingress, the host guard defers to the check above), it is secure
(`__Host-meshloom_session` with `Secure`, `Path=/`, no `Domain`), `X-Forwarded-Host`
never widens same-origin, and HSTS is never sent (the relay owns it). Responses drop
the hop-by-hop headers, `strict-transport-security` and `x-meshloom-pass`.

**HTTP streams.** The request body is handed to the app in its frames; a `RESP_HEAD`
waits for the first body message, so a response without a body is one `RESP_HEAD`
with `END` (204, 304, `HEAD`). A head above 64 KiB becomes `CANCEL frame_too_large`.
An app exception before the head answers 500; after it, `CANCEL internal`. A `CANCEL`
from the relay (or the link going down) gives the app `http.disconnect`, and later
sends are dropped, as uvicorn does.

**WebSocket streams.** `websocket.accept` sends `WS_ACCEPT 101`; a close before accept
(or the app returning) sends `WS_ACCEPT 403` with `END`, an exception 500; the
`websocket.http.response` extension is offered for denials with a body. Messages are
cut at `max_chunk` with `MORE`, reassembled on the way in (above 32 MiB: `WS_CLOSE
1009`; invalid UTF-8 text: 1007). A relay `WS_CLOSE` is echoed with the same code and
reaches the app as `websocket.disconnect`; an app close waits 5 s for the relay's
echo, then the app sees the end anyway. A send after the end raises
`RelayStreamClosed` (an `OSError`, like uvicorn's `ClientDisconnected`).

**Credits and batching.** Each stream and direction starts at `initial_credit`; the
instance grants back what the app consumed once half the window is used, and answers
an overrun, a zero or an overflowing `CREDIT` with `CANCEL credit_exceeded` (the link
stays up). Outbound frames are queued for 50 ms and sent as one message (flushed
earlier before passing 1 MiB, and at once for a final `RESP_HEAD`, a `WS_ACCEPT`, a
`CANCEL` or a `WS_CLOSE`). Even streams, unknown streams (the last 256 closed ones
silently), unknown frame types and text messages are ignored and counted
(`RelayLink.counters`); steps 1 to 3 of the contract's decoding, and an over-limit
frame on stream 0 or an even stream, close with `1002` and the reason; an over-limit
frame on an odd stream is `CANCEL frame_too_large` on that stream only. When the link
drops, every stream ends at once and the app tasks get 5 s before they are cancelled.

**Close codes and states** (`link_status` in `GET /api/remote`, `LINK_STATES`):

| Close | Behaviour | `link_status` |
|---|---|---|
| (connecting) | | `connecting` |
| HELLO done | | `connected` |
| `4001` | New token and reconnect at once; backoff if it repeats | `reconnecting` |
| `4003` | `register()` again (idempotent), reconnect once; a second `4003` or a failed registration stops | `not_registered` |
| `4004 quota_exceeded` | Retry after 15 to 20 min (or when Remote changes); the relay lifts it on the 1st (UTC) | `quota_exceeded` |
| `4004`, any other reason (`instance_paused`, `instance_frozen`) | Retry after 15 to 20 min (or when Remote changes) | `paused` |
| `4005` | Reconnect once; replaced again stops (two installs share a key). A connection that lasted 15 min (`REPLACED_RESET_AFTER_S`) starts the count again, also when it is the one that was replaced | `replaced` |
| `4009` | Stop, never retried on its own | `banned` |
| `4010` | Stop until Meshloom is updated | `update_required` |
| `1013`, unknown `4xxx` | Retry after 5 to 15 min | `relay_paused` |
| `1002`, `1009` | Logged as a protocol error, then the backoff below | `reconnecting` |
| anything else, HTTP refusals | Backoff from 1 s doubling to 5 min, full jitter, reset after 60 s connected; a 429 waits at least its `Retry-After` (at most 15 min) | `reconnecting` |

`link_status` is null while Remote is off, `community_off` while it is on with Community
off, and `not_registered` while it is on without a slug. A stopped state stays until
Remote changes (enable, disable, register, rotate, unregister, restore) or Community is
turned on or off, which restarts the loop.

**Why the close reason.** meshloom-remote sends `4004 quota_exceeded` at connect and on a
live socket, and `instance_paused` or `instance_frozen` otherwise (`src/hosts/relay.ts`,
`closeFor` in `src/relay/instanceRelay.ts`). It says as much as `paused_reason` in
`GET /v1/instances/self`, so the client makes no lookup. Any other reason shows `paused`,
never the quota.

**Why 15 min for `4005`.** Two installs on one key both reconnect at once, so each is kicked
within seconds of its HELLO. A peer that cannot connect at once waits at most one backoff
(5 min) plus the timeouts, so 15 min covers that slow fight too. `hello_ok` is no reset: every
connection of a fight has it. A single late takeover (a restored copy started and stopped) is
not a fight and never stops Remote; a copy that is rarely restarted is detected, since the
ejected install comes back at once and kicks it. Known limits:

- A fight where each side takes over 15 min to come back never ends: every connection is then
  long enough to reset the count.
- What is left of BUG-12: on a link where every connection lasts under 15 min, two `4005` far
  apart still stop Remote. It takes a real second install on the key to get a `4005` at all.

## Alert e-mail (MSHL-467)

The relay's alert e-mail (`remote-registration.md`, `PUT /v1/instances/self/alert-email`).
Today it mails the quota warning at 80 % and the pause notice; the offline alert is only
planned in the contract and not sent by meshloom-remote (`src/alertEmail.ts`), so the UI, the
relay's pages and the user docs do not promise it. All three routes are
`admin` and answer `{state}`, `none`, `pending` or `confirmed`:

| Route | Registry | Notes |
|---|---|---|
| `GET /api/remote/alert-email` | `GET /v1/instances/self` | Reads `alert_email.state`. The settings call it once per panel mount, not in the 15 s poll: `GET /api/remote` stays free of requests |
| `PUT /api/remote/alert-email {email}` | `PUT .../alert-email` | Trimmed, then checked like the relay's `EMAIL` (at most 254 characters): 400 `remote_alert_email_invalid` before any request. The first tag of `Accept-Language` (`fr`, `en`) picks the mail's first language |
| `DELETE /api/remote/alert-email` | `DELETE .../alert-email` | Forgets the address |

- 409 `remote_disabled`, `remote_not_registered`, `remote_community_off`: nothing leaves.
- Relay refusals: `invalid_request` as above; `instance_frozen` 403
  `remote_alert_email_frozen`; `registration_paused` 503 `remote_alert_email_paused`;
  `instance_unknown` 409 `remote_not_registered`; 429 `remote_rate_limited` with
  `retry_after` (3 `PUT` a day per key, 3 mails a day per address, 100 for the registry).
  A pause does not block these routes.
- `rotate` calls the same `DELETE` first when Remote is on, because the old key is the only way
  to reach the address and the relay has no purge. The relay allows it during a pause, unlike
  `DELETE /v1/instances/self`, and refuses it during a freeze or a ban (an operator ban does not
  clear the address either); a failure never blocks the rotation. With Remote or Community off
  nothing may leave. In all these cases the address stays on the relay under a key this instance
  no longer has, and only a still-valid stop link removes it (`TOKEN_TTL_S` 24 h in the
  confirmation mail, `STOP_LINK_TTL_S` 30 days in an alert mail). The user docs say so.
- The address is never stored here, logged or put in the audit. The relay never returns it,
  so there is no read of it. Audit: `remote.alert_email_set`, `remote.alert_email_cleared`,
  with `details.error` on a failure.

## Custom domain test (MSHL-428)

A CNAME from a user's domain to the relay fails without Cloudflare for SaaS (error 1014),
so the supported recipe is a user-side URL redirect to `https://<slug>.meshloom.link`
(`docs/user/{en,fr}/deep/remote-domain.md`; no custom hostname, no cost). The "Test"
button, `POST /api/remote/domain/test {domain}` (`remote.check_custom_domain`), checks it
from the instance:

- 409 `remote_disabled` while Remote is off and `remote_not_registered` without a
  hostname; nothing leaves then (the sweep in `tests/test_remote_off_no_egress.py` covers
  the route). Nothing is ever sent to the relay: the only destination is the user's
  domain.
- `normalize_domain` accepts a bare DNS name (an `https://` prefix and a trailing `/` are
  stripped, IDNA A-label); an IP literal, a port, a path or a single label is
  `invalid_domain`, without any request.
- One `GET` per scheme, HTTPS first, HTTP only after a connect or TLS error, through the
  egress guard as a user-chosen destination (private, loopback and metadata addresses
  refused, IP pinning), redirect never followed, body never read, 20 s in all.
- `result`: `ok` (a 301/302/303/307/308 whose `Location` host is exactly
  `remote_state.hostname`; path and scheme ignored), `no_redirect` (any other answer, or a
  redirect without `Location`), `wrong_target` (`target_host` is the host found; untrusted
  text), `invalid_domain`, or `unreachable` with `detail` an `error_code`
  (`egress_unresolvable`, `egress_private_address`, `timeout`, `tls_error`,
  `connect_error`...). Translations: `settings.remote.domain.result.*` and `.reason.*`.
- Rate limit: one instance-wide limiter (`take_domain_test_slot`, aiolimiter, never
  queued): 1 test per 5 s and 6 per minute. Over it the route answers 429 `rate_limited`
  with `Retry-After` (the panel shows `errors.rate_limited`, FR and EN) and sends nothing,
  so an admin session, relay included, cannot use the instance as a request cannon.
- The domain is not stored: it is not a setting.

## Permissions and audit

`admin`: enable, disable, unregister, rotate, revoke-all, the custom domain test. Every account role but `kiosk`
(`own_account()`): the state (an administrator sees every device, anyone else their
own), enrolment, client passes, own revocations, access requests. Audit rows: the
`remote.*` actions in `docs/dev/auth.md`. Never log or audit a pass, a token, an
enrolment URL or an access code.

## Settings UI (`frontend/src/components/settings/SettingsRemoteSection.tsx`)

Settings → Remote access (section `remote`, lazy-loaded, every account role but the kiosk:
`ACCOUNT_SETTINGS_SECTIONS`). Details in `frontend/AGENTS.md`, "Remote access section".

- One actionable line (turn on 2FA; a passkey does not stop a password alone) and a "Learn more" link to the user docs
  (`docs/user/<lang>/deep/remote.md`, URL from `utils/userDocsUrl.ts`). What the relay sees (TLS
  ends at the relay, so it could read the traffic, the password at sign-in included; what the
  registration sends; a relayed request is never the local network) lives in that page and
  the relay's terms, not in the UI (MSHL-449).
- State badge from `state` and `link_status` (`remoteShared.ts` `remoteBadge`): every
  `LINK_STATES` value, `off`, `needs_community`, `unregistered` and `unknown` has a label
  and a help in `settings.remote.badge.*` / `badgeHelp.*`, and an action where one helps
  (enable or retry, off and on again for `not_registered`, replace the key for `replaced`,
  open Updates for `update_required`, open the Community settings for `needs_community` and
  `community_off`). With `community_enabled` false the badge is `needs_community` (Remote
  off: no enable button) or `community_off` (Remote on, paused). Hostname with copy, key
  fingerprint.
- Administrators: enable (a two-line dialog, without the relay paragraph; it blocks until
  acknowledged while plugins run with reduced isolation, the rule of the
  `remote_reduced_plugin_isolation` notice),
  disable, unregister, rotate, revoke-all, each confirmed.
- Enrolment QR (`RemoteEnrollDialog`): the URL stays in the dialog's state only; a local
  15 min countdown; polls `GET /api/remote` every 3 s and reports the new `qr` device.
  Through the relay the button is replaced by the LAN-only notice.
- Custom domain (`RemoteDomainPanel`, administrators, registered only): one input and the
  "Test" button; the result box shows the translated outcome (`data-result`).
- Access requests (`RemoteAccessRequestsPanel`): the pending list never shows the code
  (the reader types what the device shows), carries a warning against social
  engineering, and renders the user agent as plain text without control or direction
  characters (`untrustedText`).
- Wording (MSHL-442, shared with the relay pages of meshloom-remote `src/i18n.ts`): the UI never says
  "enrol" or "pass". A pass is the device's *access*; enrolment and approval both *allow a device*
  (fr *autoriser un appareil*), listed as *Allowed devices* (*Appareils autorisés*); unregister is
  *Leave Meshloom Remote* (*Quitter Meshloom Remote*); devices are any device or browser, never
  "phone" only. Copy stays short (MSHL-447 rules): one sentence per help or dialog, details in
  the user docs. The code keeps the technical names (`enroll`, pass).
- Error codes: `errors.<code>` for every `REMOTE_ERRORS` and relay refusal code (slice
  `remote`). `tests/test_remote_ui_strings.py` fails when a code or a link state has no
  French or English string, or when `remoteTypes.ts` drifts from `LINK_STATES`.

## Not done here

- Issuing a client pass (`POST /api/remote/devices/client`) has no UI yet.
- CONTROL `instance_update` when the plugin settings change without a reconnect: the
  next HELLO carries them. Closing the instance's own open WebSockets of a revoked
  device: the relay cancels those streams after a `gate`, and every new request checks
  the pass again.
- A custom relay URL (MSHL-99); the remaining relay-aware hardening (MSHL-84).
- Guest enrolment links (up to 7 days).

## Tests

`tests/test_remote_identity.py` (vectors: passes, access codes, a local re-implementation
of the registry's JWT checks proved on every case, then the minted tokens),
`tests/test_remote_service.py` (fake registry, fake relay and edge: registration,
enrolment, access requests, revocation, rotation, restore, routes, alert e-mail),
`tests/test_remote_off_no_egress.py`, `tests/test_community_attestation.py` (the
attestation header, renewal, grace fallback and errors against a fake registry),
`tests/test_community_required.py` (Community off:
enable refused, no registry request, the relay link follows the opt-in, restore),
`tests/test_remote_domain.py` (the custom domain
test: results, scheme fallback, private addresses, off), `tests/test_migrations/test_migration_138.py`.
Settings UI: `frontend/src/test/settingsRemoteSection.test.tsx` (mocked `/api/remote`
states) and `tests/test_remote_ui_strings.py`.

Relay client: `tests/test_remote_frames.py` (the codec on every frame, message and
invalid-message vector, the limits, the HELLO role rules) and
`tests/test_remote_relay.py`, against the in-process fake relay of
`tests/remote_relay_fakes.py` (a `websockets` server): handshake, relayed GET/POST with
credits and batching, CANCEL, WebSocket sessions, the pass check, protocol errors, every
close code, and through the real app the never-LAN rule, the `__Host-` session cookie
at login and `X-Forwarded-Host` ignored. Egress in both directions:
`tests/test_remote_off_no_egress.py` and `tests/test_community_off_no_egress.py`.
