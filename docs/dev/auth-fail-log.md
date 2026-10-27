# The `AUTH_FAIL` log line

One log line per refused credential, stable enough for a log watcher (fail2ban,
CrowdSec, the shipped fail2ban filter, MSHL-423) to key on the client address.
The built-in ban (MSHL-422, `app/auth/ip_ban.py`, `docs/dev/auth.md` "IP ban")
consumes the same events: `fail_log` hands it the fields of each line it
writes, so it counts exactly the `proxied=0` lines, with the same address, and
never a local one.
Decided as a contract with the Accès scoping (remote propositions v2, §5.3 and
decision 6, MSHL-421). Code: `app/auth/fail_log.py` (rules in its header) and
the call sites listed under "Routes". Tests: `tests/test_auth_fail_log.py`,
which pins the format with a fail2ban-style regex and checks that this page
names every route and reason below. Like every doc here, the code wins; a change
to this contract is a `CONTRACTS:` commit.

## Contract version

**Contract version 1** (MSHL-421).

- Adding or removing a route or a reason keeps the version: a consumer keys
  on `ip` and `proxied` and must accept any `route` and `reason`. It is listed
  below.
- Removing or renaming a field, changing its order, the marker, the logger
  name or the level raises the version.

| Version | Change |
|---|---|
| 1 | First version (MSHL-421): `via` is the transport; `proxied=1` only for a local peer that is not a declared proxy. |
| 1 | Route `basic` removed with HTTP Basic auth (MSHL-100): a `Basic` header is no credential and writes no line. |

## The record

A `WARNING` record on the logger `meshloom.auth` whose message is exactly:

```
AUTH_FAIL ip=<ip> route=<route> reason=<reason> via=<via> proxied=<0|1> user=<user>
```

Fields are separated by one space, in this order, and no value contains a
space or any other control character (see "Escaping").

| Field | Value |
|---|---|
| `ip` | The client address as the auth gate sees it, `client_ip()`: the TCP peer, or the client a declared trusted proxy forwarded (`docs/dev/auth.md`, "Proxy headers"). Canonical form (`2001:db8::1`, lower case); an IPv4-mapped IPv6 address is shown as IPv4. `-` when there is none, it is not an IP address, or it carries an IPv6 zone id (`fe80::1%eth0`: Python keeps any text after the `%`), so no request data reaches the line as free text |
| `route` | Where the credential was presented, see "Routes" |
| `reason` | Why it was refused, from the closed list of its route |
| `via` | The transport: `lan` or `relay` (`request_via()`), never the provider that signed the request in (a request a trusted-network rule signs in is `lan`). Home Assistant ingress never writes the line: its peer is the Supervisor, which must never be banned |
| `proxied` | `1` when the TCP peer is a local address (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `100.64.0.0/10`, `127.0.0.0/8`, `169.254.0.0/16`, `::1`, `fc00::/7`, `fe80::/10`; `LOCAL_NETWORKS`), is not a declared trusted proxy, and sends `X-Forwarded-For`, `X-Real-IP` or `Forwarded`: most likely a local reverse proxy nobody declared, so `ip` is that proxy, shared by every client behind it: **a consumer must ignore `proxied=1` lines**. `0` otherwise, including a public peer that sends those headers: it is the client itself, `ip` is that peer, and adding a header never escapes a ban |
| `user` | `h:` and 6 lower-case hex characters: an HMAC-SHA256 of the typed name, folded like a username (`username_key()`), under a random key drawn at each start. `-` when the route takes no name. Never the name itself (someone typing a password into the name field must not leave it in the logs). The hash groups attempts on one name within one run and cannot be checked against a guess offline; it changes at each restart |

## Routes

| `route` | Where | `reason` |
|---|---|---|
| `login` | `POST /api/auth/login`, wrong name or password (also a disabled or ingress-only account) | `invalid_credentials` |
| `login_2fa` | `POST /api/auth/login/2fa`: wrong TOTP or recovery code | `bad_code` |
| `login_2fa` | `POST /api/auth/login/2fa`: refused passkey as the second factor | `bad_passkey` |
| `login_2fa` | `POST /api/auth/login/2fa` or `POST /api/auth/webauthn/login/options` with a pending token that is unknown, expired, stale or presented from another client | `pending_invalid` |
| `passkey` | `POST /api/auth/webauthn/login/verify`: unknown or used challenge, or an assertion that does not check | `bad_passkey` |
| `bearer` | `Authorization: Bearer`: unknown, revoked or expired API or device token | `invalid_token` |
| `pair` | `POST /api/auth/pair`: unknown, malformed (within the body's length limit), used or expired pairing code (410) | `invalid_code` |
| `setup` | `POST /api/auth/setup`: wrong installation code | `invalid_setup_code` |

Not written:

- an attempt refused by a login delay before any check (429
  `auth_rate_limited`), or by the busy argon2id queue (503 `auth_busy`);
- a precondition: setup already done (409), a full token table on pairing
  (409), a route closed under ingress (403);
- a body that fails validation (422), such as a pairing code longer than the
  body allows;
- anything under ingress;
- a password or code checked on an authenticated route (re-authentication,
  enrolment), a kiosk exchange, an expired or unknown session cookie.

## Escaping

Untrusted text must never produce a line of its own (MSHL-432). Two layers, both
tested in `tests/test_log_forging.py` against the regex of this page and the
shipped filter:

- **Every record, every handler.** The formatter of each app handler (standard
  error and so the journal, the uvicorn access log, the in-app log view;
  `RedactingFormatter`, `app/log_redaction.py`) writes every control character
  of the record (the message with its arguments, however they were formatted,
  and the text of an exception, its chain, notes and groups) as a visible
  escape: `\n`, `\r`, `\t` by name, `\xNN` for the other C0 and C1 characters
  and DEL, `\uNNNN` for U+2028 and U+2029. The only line breaks left in a
  record are those between the message line and the structural lines of a
  traceback (its frames); an exception message stays on one line. A
  handler added later must go through `install_redacting_formatter()`.
- **Every `AUTH_FAIL` field.** `format_line()` raises, and writes nothing, for a
  `route`, `reason` or `via` outside `[a-z0-9_]+`, or when the finished line has a
  character outside `[A-Za-z0-9_.:-]` apart from its spaces. `ip` can only be a
  canonical address or `-` (a zone id is refused, see the table), `user` only
  `-` or `h:` and hex digits. So no value a client chooses can contain a line
  break, a space or the marker.

A consumer may therefore treat each line of the log as one record. The built-in
ban is not fed from the log text: `fail_log` hands it the same fields in
process, and `canonical_address()` refuses anything that is not a plain address,
so text that reaches the log cannot add a failure to any address.

## The full line

The handlers (`app/config.py`) write
`%(asctime)s - %(name)s - %(levelname)s - %(message)s` with the date as
`%Y-%m-%d %H:%M:%S` in the host's local time, to standard error (the journal on
a package install, the container log under Docker). That prefix is the logging
setup, not this contract. The full line (Python regex, `host` is the address
to act on); the test reads it from this page:

```
^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - meshloom\.auth - WARNING - AUTH_FAIL ip=(?P<host>\S+) route=\S+ reason=\S+ via=\S+ proxied=0 user=\S+$
```

It matches a record rendered through the same formatter and never a
`proxied=1` line. The shipped fail2ban filter (MSHL-423,
`pkg/nfpm/fail2ban/meshloom-filter.conf`, installed disabled by the deb and rpm
packages) is this regex with two changes, which
`tests/test_fail2ban_filter.py` and `scripts/test/fail2ban_filter.sh` (real
`fail2ban-regex`, run in `.github/workflows/nfpm.yml` on the unpacked `.deb`)
hold to this page and its Example below:

- `ip=<ADDR>` for `(?P<host>\S+)`;
- an optional leading `<host> <ident>[<pid>]: ` and an optional date: with the
  systemd backend fail2ban hands the filter the journal line with the message
  unchanged, date included; with a log file it cuts the date off before matching.

It also needs a `datepattern` of `%%Y-%%m-%%d %%H:%%M:%%S`, local time, not the
ISO UTC form of the scoping example. The jail reads the journal of
`meshloom.service` (`journalmatch = _SYSTEMD_UNIT=meshloom.service`); under Docker
the logs must reach the journal (`logging: driver: journald`), see
`docs/user/en/deep/fail2ban.md`.

## Example

```
2026-10-27 10:15:02 - meshloom.auth - WARNING - AUTH_FAIL ip=203.0.113.7 route=login reason=invalid_credentials via=lan proxied=0 user=h:3f9a1c
2026-10-27 10:15:09 - meshloom.auth - WARNING - AUTH_FAIL ip=203.0.113.7 route=login_2fa reason=bad_code via=lan proxied=0 user=h:3f9a1c
2026-10-27 10:16:40 - meshloom.auth - WARNING - AUTH_FAIL ip=172.18.0.1 route=bearer reason=invalid_token via=lan proxied=1 user=-
```
