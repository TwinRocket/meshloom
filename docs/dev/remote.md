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

This page covers what MSHL-79 built: identity, registration, passes, enrolment, access
requests, revocation and the control API. The relay client (the outbound socket,
HELLO, frame dispatch: MSHL-87) and the settings UI (MSHL-82) build on it.

## Code map

| File | Role |
|---|---|
| `app/services/instance_identity.py` | The `remote` instance key (`app_settings.remote_instance_seed`), `mint_remote_jwt()`, `sign_with_instance_key()`. The only identity module |
| `app/services/remote_passes.py` | Pass format, the contract verifier `verify_pass`, access code normalisation. Pure functions |
| `app/services/remote.py` | Opt-in, registry client, state changes, devices, enrolment, access requests, CONTROL ops, the relay link interface, the `remote` on_restore hook |
| `app/repository/remote.py` | `remote_state`, `remote_devices`, `remote_enroll_tokens` (migration 138, `docs/dev/database.md`) |
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

## State changes

| Call | Route | Effect |
|---|---|---|
| `enable` | `POST /api/remote/enable` | Turns Remote on and registers (`POST /v1/instances` with `version`, `install_kind`, `plugins: {enabled, isolation}`) when not registered. Stores `slug`, `hostname`, `relay_url` and declares the hostname to the host guard. A registry failure keeps Remote on, unregistered, with `last_error`; calling it again retries |
| `register` | (relay client) | The same registration, idempotent: after a close `4003` |
| `disable` | `POST /api/remote/disable` | Turns Remote off and withdraws the hostname; registration and devices are kept |
| `unregister` | `DELETE /api/remote` | `DELETE /v1/instances/self` (a 404 `instance_unknown` counts as done), forgets slug and hostname, turns Remote off, **keeps the key** (the registry holds the slug 30 days for it). 409 `remote_disabled` while off |
| `rotate` | `POST /api/remote/rotate` | Deletes the old registration while the old key still signs (best effort), replaces the key, forgets every device, enrolment token and access request, resets the generation, and registers again when Remote is on (a new slug) |
| on_restore `remote` | any restore | The `instance_identity` hook replaced the key; this one forgets registration, devices and tokens and turns Remote off. A database copy also empties the three tables (`SNAPSHOT_EMPTIED_TABLES`) |

`GET /api/remote` returns `state`: `disabled`, `unregistered` (on, no slug) or
`registered`, plus `relay_connected` and `link_status` as the relay client reports them.

## Device passes

Format and rules: `remote-relay.md`, "Passes". The instance signs:

| Kind | Lifetime | Issued by |
|---|---|---|
| `enroll` | 15 min | `POST /api/remote/enroll` (QR code) |
| `browser` | 90 days | a QR enrolment or an approved access request (the relay sets `__Host-mlgate`) |
| `client` | 365 days | `POST /api/remote/devices/client` (the `X-Meshloom-Pass` header of a script or meshenger), shown once |

`iss` is the slug, `sub` the device id (22 base64url characters), `uid` the Meshloom user
id the device belongs to, `gen` the current generation. Every device is a row of
`remote_devices`.

`verify_device_pass(token, slot)` is the third check of the edge lock, for the relay
client to run on `x-meshloom-pass`: the contract verdict, then the device must exist,
not be revoked and belong to the pass's `uid`.

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
  presented (until `pass_expires_at` + 60 s). Past 1024 ids (the relay's limit), the
  generation is bumped instead.
- `POST /api/remote/devices/revoke-all` (admin) bumps `gate_gen`: every pass dies.
- `gate_state()` is `{gen, revoked}`. Each change is pushed at once as CONTROL `gate`
  when a relay link is up (`gate_pushed` in the answer); otherwise the next HELLO
  carries it.

## Relay link interface (for MSHL-87)

- `set_relay_link(sender, status)`: the async callable that sends one CONTROL op
  (returns True when sent), or None when disconnected; `set_link_status(status)` for
  the UI states of the close codes (`replaced`, `banned`, `update required`...).
- `handle_control(frame)`: handles `enroll` (returns the `pass` reply to send),
  `access_request` and `status` (a new name from the operator); other ops are ignored.
- `instance_info()`, `plugins_info()` and `gate_state()` fill the HELLO.
- `register(actor)` after a `4003`; `add_change_listener(callback)` to connect or
  disconnect when Remote is enabled, disabled, registered, rotated, unregistered or
  reset by a restore.

## Permissions and audit

`admin`: enable, disable, unregister, rotate, revoke-all. Every account role but `kiosk`
(`own_account()`): the state (an administrator sees every device, anyone else their
own), enrolment, client passes, own revocations, access requests. Audit rows: the
`remote.*` actions in `docs/dev/auth.md`. Never log or audit a pass, a token, an
enrolment URL or an access code.

## Not done here

- The relay client and the in-process dispatch (MSHL-87), the settings UI and its
  translations of the error codes (MSHL-82), the user documentation (MSHL-83).
- Guest enrolment links (up to 7 days), pass renewal through `x-meshloom-set-pass`, the
  alert e-mail routes of the registry.
- A device of a deleted account keeps a valid pass until it is revoked; the sign-in
  behind the edge lock still refuses it.

## Tests

`tests/test_remote_identity.py` (vectors: passes, access codes, a local re-implementation
of the registry's JWT checks proved on every case, then the minted tokens),
`tests/test_remote_service.py` (fake registry, fake relay and edge: registration,
enrolment, access requests, revocation, rotation, restore, routes),
`tests/test_remote_off_no_egress.py`, `tests/test_migrations/test_migration_138.py`.
