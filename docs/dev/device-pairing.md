# Device pairing

The contract between a Meshloom instance and a device that enrols itself by
scanning a QR code: meshenger first (ADR 001, epics E1 and E2), and any app or
script that wants a device token without anyone typing one. It lives here, not
in meshloom-community's `docs/contracts/`: those cover Meshloom and the
TwinRocket services, and meshenger is a docs-only repository that points to
this page (owner decision Q14). Code: `app/auth/pairing.py` (rules in its
header), `app/routers/auth_pairing.py` (routes), `PairingCodeRepository`
(`app/repository/auth.py`), table `pairing_codes` (migration 112,
`docs/dev/database.md`). Tests: `tests/test_auth_pairing.py`, which also checks
that this page names every route, error code and limit below. Like every doc
here, the code wins; a change to this contract is a `CONTRACTS:` commit.

## Flow

1. Someone signed in to Meshloom (a `member` or an `admin`) creates a pairing
   code: a label, the scopes the device will get, a lifetime of at most 10
   minutes. The instance answers the code once, with the text of the QR.
2. The device scans the QR, decodes the payload, and posts the code to
   `POST /api/auth/pair` on one of the payload's addresses.
3. The instance spends the code and answers a device token (`mlt_…`) that
   belongs to the account that created the code, with exactly its scopes.
4. The device then sends `Authorization: Bearer mlt_…` on every HTTP request
   and on the WebSocket upgrade (`docs/dev/auth.md`, "API and device tokens").

The code works once, whatever the outcome of that one attempt.

## QR payload

```
meshloom://pair?d=<base64url of the JSON below, no padding>
```

The JSON is compact (no spaces), UTF-8, keys in this order:

```json
{"v":1,"urls":["https://mesh.example.org","http://192.168.1.10:8000"],"code":"mlp_q3Xb7Kd1Vn0aT9cW2yLh4g"}
```

which gives:

```
meshloom://pair?d=eyJ2IjoxLCJ1cmxzIjpbImh0dHBzOi8vbWVzaC5leGFtcGxlLm9yZyIsImh0dHA6Ly8xOTIuMTY4LjEuMTA6ODAwMCJdLCJjb2RlIjoibWxwX3EzWGI3S2QxVm4wYVQ5Y1cyeUxoNGcifQ
```

| Field | Meaning |
|---|---|
| `v` | Payload version, `1`. A client refuses a version it does not know. |
| `urls` | Base addresses of the instance, best first, at most 8: the declared public addresses (`auth_settings.public_urls`, Settings → Local Configuration, `https` only, possibly with a path prefix), the Meshloom Remote hostname as `https://<host>` when the instance is linked, then the address the creator's browser used when it is a LAN one (a direct request, not Home Assistant ingress or the relay, whose `Host` names this instance and is not loopback). Fixed when the code is created. May be empty (created from `localhost` with no public address): the client then asks the user for the address. |
| `code` | The pairing code: `mlp_` + 128 random bits in base64url (26 characters in all). |

A client tries the addresses in order and posts the code to the first that
answers; `<url>/api/auth/pair` (a path prefix is kept). It should only send the
code over `https`, or over `http` to a private (LAN) address the user is on.

## Routes

Errors are `{"detail": {"code", "message", "params"?}}`, as everywhere in
`/api/auth` (`docs/dev/auth.md`, "Routes"). The message is English and only a
hint; clients switch on `code`.

### `POST /api/auth/pairings`

Signed-in account (`own_account()`: a session, never a token), role `member`
or `admin`. Body:

| Field | Type | Rule |
|---|---|---|
| `label` | string | 1 to 64 characters, trimmed. Shown in the list of pending codes. |
| `scopes` | list | 1 to 5 of `read`, `read_private`, `send`, `repeater_admin`, `admin`, each granted by the creator's role. |
| `ttl` | integer | Seconds, 30 to 600; default 300. |

201: `{id, label, scopes, urls, created_at, expires_at, created_by, username,
code, qr_payload}` (Unix seconds). `code` and `qr_payload` are only in this
answer. Errors: 401 `auth_required` (not signed in), 400 `auth_token_scope` (`params.scope`, a scope the role does
not grant), 403 `auth_forbidden` (a `viewer`, a `kiosk`, or a token), 409
`auth_pairing_limit` (`params.max`: 10 pending codes per account), 422 for a
malformed body.

### `GET /api/auth/pairings`

Same access. The pending codes (not used, revoked or expired), newest first:
the caller's own; every account's for an admin. Same fields as the creation
answer without `code` and `qr_payload`.

### `DELETE /api/auth/pairings/{id}`

Same access. 204: the code stops working at once. 404
`auth_pairing_not_found` for an unknown id, a code that is no longer pending,
or another account's code when the caller is not an admin.

### `POST /api/auth/pair`

Public (no account, no cookie). Body:

| Field | Type | Rule |
|---|---|---|
| `code` | string | The `code` of the payload, at most 128 characters. |
| `device_name` | string | 1 to 64 characters, trimmed: the device token's name in the list of tokens and in the audit log. |
| `extensions` | object, optional | Opaque to the instance, at most 4096 bytes as compact JSON. Stored with the pairing for meshenger E2 (linked keys); not returned. |

201:

```json
{
  "token": "mlt_…",
  "token_id": "4f1c…",
  "scopes": ["read", "send"],
  "user": {"id": 2, "username": "nadia", "display_name": "Nadia"},
  "urls": ["https://mesh.example.org", "http://192.168.1.10:8000"]
}
```

`token` is shown in this answer only; store it in the platform's secure
storage. `urls` are the payload's. The token has no expiry, origin `pairing`,
and the scopes of the code; it belongs to `user`, the code's creator.

Errors, checked in this order:

| Status | Code | When |
|---|---|---|
| 422 | (validation) | Malformed body. The code is not spent. |
| 403 | `auth_pairing_ingress` | The request came through the Home Assistant Supervisor (ingress). The code is not spent: pair on the instance's own address. |
| 429 | `auth_rate_limited` | A login delay runs for this address (`params.retry_after`, `Retry-After`). The code is not spent. |
| 410 | `auth_pairing_expired` | Every other bad code: unknown, malformed, already used, revoked, expired, or its creator can no longer hold such a token (disabled, deleted, demoted below a scope, role without tokens). One answer for all, so it tells nothing about which. |
| 409 | `auth_token_limit` | The creator already holds the maximum of live tokens (`params.max`, 100). The code is spent. |

On 410 the client tells the user to show a new code; it never retries the
same code.

## Security rules

- **Single use, in one transaction.** The redemption spends the code and
  inserts the token in one database transaction: two concurrent attempts with
  one code give one token. A code found pending is spent whatever follows.
- **Hashed at rest.** Only the SHA-256 of the code is stored; the lookup goes
  by that hash and compares with `hmac.compare_digest`. Neither the code nor
  the token appears in a log or an audit row.
- **Expiry and revocation are immediate**: the expiry is compared at the
  redemption, a revocation is one `UPDATE`.
- **Never more than the creator.** The scopes are checked against the
  creator's role at creation and again at the redemption, and at each use the
  token never exceeds its owner's current role (`permissions_of()`).
- **Rate limited** by the login delays (`app/auth/ratelimit.py`,
  `docs/dev/auth.md`, "Login delays"): each bad code counts as a failed login
  for the key `\x00pairing` (no username can be it), so the progressive delay
  per address, the global slow-down and the per-address budget shared with
  `POST /api/auth/login` all apply.
- **Never through ingress.** The Supervisor serves one shared origin and signs
  its users in itself; a device token is refused there anyway.
- **Audit.** `pairing.created`, `pairing.revoked`, `pairing.used` rows
  (`success`, `failure` with `details.reason` `unknown`, `malformed`, `used`,
  `revoked`, `expired`, `owner_unavailable`, `owner_role`, `scope`,
  `token_limit`; `refused` under ingress; one `delayed`/`refused` row per
  login delay). A successful `pairing.used` row is written as the new token
  (`actor_token_id`) and names the device (`details.device`).
- **Backups.** Pairing codes are never in the JSON backup and are emptied in
  the database download; the device tokens travel in the `api_tokens` section.
  The hourly housekeeping deletes the codes that hold no token and can no
  longer give one.

## Using the token

- **HTTP:** `Authorization: Bearer mlt_…` on every request. Never in a query
  string or a cookie (ignored). Bearer requests are exempt from the CSRF
  origin rule.
- **WebSocket:** the same header on the `/api/ws` upgrade. The socket gets the
  events its scopes allow (a `read` token gets no direct message), and closes
  with 1008 when the token is revoked.
- **Revocation:** the owner (or an admin) revokes the device in the "API and
  device tokens" list of the settings, or with `DELETE /api/auth/tokens/{id}`. The device then gets
  401 `auth_invalid_credentials`; it should drop the token and offer to pair
  again.
- **Meshloom Remote.** The Remote edge gate (the relay's lock)
  lets a request carrying `Authorization: Bearer` through without the portal's
  cookie; the instance still checks the token. Remote is not in Meshloom yet:
  until it is, a device uses the public or LAN addresses.
