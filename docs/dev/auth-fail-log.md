# The `AUTH_FAIL` log line

One log line per refused credential, stable enough for a log watcher (fail2ban,
CrowdSec, the built-in ban planned in MSHL-423) to key on the client address.
Decided as a contract with the Accès scoping (remote propositions v2, §5.3 and
decision 6, MSHL-421). Code: `app/auth/fail_log.py` (rules in its header) and
the call sites listed under "Routes". Tests: `tests/test_auth_fail_log.py`,
which pins the format with a fail2ban-style regex and checks that this page
names every route and reason below. Like every doc here, the code wins; a change
to this contract is a `CONTRACTS:` commit.

## Contract version

**Contract version 1** (MSHL-421).

- Adding a route or a reason keeps the version: a consumer keys on `ip` and
  `proxied` and must accept any `route` and `reason`. It is listed below.
- Removing or renaming a field, changing its order, the marker, the logger
  name or the level raises the version.

| Version | Change |
|---|---|
| 1 | First version (MSHL-421). |

## The record

A `WARNING` record on the logger `meshloom.auth` whose message is exactly:

```
AUTH_FAIL ip=<ip> route=<route> reason=<reason> via=<via> proxied=<0|1> user=<user>
```

Fields are separated by one space, in this order, and no value contains a
space.

| Field | Value |
|---|---|
| `ip` | The client address as the auth gate sees it, `client_ip()`: the TCP peer, or the client a declared trusted proxy forwarded (`docs/dev/auth.md`, "Proxy headers"). Canonical form (`2001:db8::1`, lower case); an IPv4-mapped IPv6 address is shown as IPv4. `-` when there is none or it is not an IP address, so no request data reaches the line as free text |
| `route` | Where the credential was presented, see "Routes" |
| `reason` | Why it was refused, from the closed list of its route |
| `via` | `lan` or `relay` (`request_via()`). Home Assistant ingress never writes the line: its peer is the Supervisor, which must never be banned |
| `proxied` | `1` when the request carries `X-Forwarded-For`, `X-Real-IP` or `Forwarded` from a peer that is not a declared trusted proxy. `ip` is then that proxy, shared by every client behind it: **a consumer must ignore `proxied=1` lines**. `0` otherwise |
| `user` | `h:` and 6 lower-case hex characters: an HMAC-SHA256 of the typed name, folded like a username (`username_key()`), under a random key drawn at each start. `-` when the route takes no name. Never the name itself (someone typing a password into the name field must not leave it in the logs). The hash groups attempts on one name within one run and cannot be checked against a guess offline; it changes at each restart |

## Routes

| `route` | Where | `reason` |
|---|---|---|
| `login` | `POST /api/auth/login`, wrong name or password (also a disabled or ingress-only account) | `invalid_credentials` |
| `login_2fa` | `POST /api/auth/login/2fa`: wrong TOTP or recovery code | `bad_code` |
| `login_2fa` | `POST /api/auth/login/2fa`: refused passkey as the second factor | `bad_passkey` |
| `login_2fa` | `POST /api/auth/login/2fa` or `POST /api/auth/webauthn/login/options` with a pending token that is unknown, expired, stale or presented from another client | `pending_invalid` |
| `passkey` | `POST /api/auth/webauthn/login/verify`: unknown or used challenge, or an assertion that does not check | `bad_passkey` |
| `basic` | `Authorization: Basic` (4.x compatibility, until it is removed): wrong credential, or an account with a second factor | `invalid_credentials` |
| `bearer` | `Authorization: Bearer`: unknown, revoked or expired API or device token | `invalid_token` |
| `pair` | `POST /api/auth/pair`: unknown, malformed, used or expired pairing code | `invalid_code` |
| `setup` | `POST /api/auth/setup`: wrong installation code | `invalid_setup_code` |

Not written:

- an attempt refused by a login delay before any check (429
  `auth_rate_limited`), or by the busy argon2id queue (503 `auth_busy`);
- a precondition: setup already done (409), a full token table on pairing
  (409), a route closed under ingress (403);
- anything under ingress;
- a password or code checked on an authenticated route (re-authentication,
  enrolment), a kiosk exchange, an expired or unknown session cookie.

## The full line

The handlers (`app/config.py`) write
`%(asctime)s - %(name)s - %(levelname)s - %(message)s` with the date as
`%Y-%m-%d %H:%M:%S` in the host's local time, to standard error (the journal on
a package install, the container log under Docker). That prefix is the logging
setup, not this contract. The full line, as the test pins it (Python regex,
`host` is the address to act on):

```
^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - meshloom\.auth - WARNING - AUTH_FAIL ip=(?P<host>\S+) route=\S+ reason=\S+ via=\S+ proxied=0 user=\S+$
```

It matches a record rendered through the same formatter and never a
`proxied=1` line. A fail2ban filter built on it (MSHL-422) needs a
`datepattern` of `%%Y-%%m-%%d %%H:%%M:%%S`, local time, not the ISO UTC form
of the scoping example.

## Example

```
2026-10-27 10:15:02 - meshloom.auth - WARNING - AUTH_FAIL ip=203.0.113.7 route=login reason=invalid_credentials via=lan proxied=0 user=h:3f9a1c
2026-10-27 10:15:09 - meshloom.auth - WARNING - AUTH_FAIL ip=203.0.113.7 route=login_2fa reason=bad_code via=lan proxied=0 user=h:3f9a1c
2026-10-27 10:16:40 - meshloom.auth - WARNING - AUTH_FAIL ip=172.18.0.1 route=bearer reason=invalid_token via=lan proxied=1 user=-
```
