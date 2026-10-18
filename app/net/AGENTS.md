# Egress guard (`app/net/`)

One guard for every outbound request to a destination a user or a browser
chose (SSRF). Like every agent doc here, check it against the code.

## Semantics (`egress_guard.py`)

- `validate_destination(url, *, allow_private_network=False, schemes={http, https})`
  parses the URL, resolves A/AAAA (`loop.getaddrinfo`) and returns a
  `ResolvedDestination` (scheme, host, port, validated IPs), or raises
  `EgressBlocked(code)` **before any connection**. Codes are stable and
  translated by the UI under `errors.<code>`: `egress_scheme` (bad scheme,
  malformed URL or port), `egress_private_address`, `egress_unresolvable`.
  `egress_detail(code)` builds the coded HTTP detail (never a host, an IP or the URL).
- Refused unless `allow_private_network`: loopback, `0.0.0.0/8`, private
  (10/8, 172.16/12, 192.168/16, fc00::/7, fec0::/10), CGNAT 100.64/10,
  link-local (169.254/16, fe80::/10), multicast, reserved 240/4, unspecified,
  IETF protocol assignments 192.0.0.0/24, benchmarking 198.18/15 (also the
  fake-IP pool of local DNS proxies such as Clash),
  NAT64 (`64:ff9b::/96`, `64:ff9b:1::/48`), 6to4 `2002::/16`, Teredo
  `2001::/32`, IPv4-compatible `::/96`, and IPv4-mapped IPv6 of those. The
  opt-in **covers loopback** (Gotify or Home Assistant on the same host).
- Refused whatever the flag: `169.254.169.254`, `fd00:ec2::254`,
  `100.100.100.200` and `metadata.google.internal`, and the documentation
  ranges (192.0.2/24, 198.51.100/24, 203.0.113/24, 2001:db8::/32). Both also
  inside an IPv6 transition form: IPv4-mapped, NAT64 (`64:ff9b::/96`,
  `64:ff9b:1::/48`), IPv4-compatible `::/96`, 6to4 `2002::/16`, Teredo.
- The host is the IDNA2008 A-label httpx puts in `Host`; DNS and SNI use it
  too (`getaddrinfo` on the Unicode name would apply IDNA 2003: `faß.de` →
  `fass.de`).
- One forbidden address among the answers refuses the host.
- Ranges are tested one by one. Never use `ipaddress.is_private` / `is_global`:
  they changed between Python 3.11 and 3.13 (mapped IPv6, 100.64/10).

## Sending (IP pinning)

- httpx: `pinned_async_client(dest, *, verify_tls=True, timeout)` yields a
  client whose `request()`/`post()` connect to a validated IP
  (`https://<ip>:<port><path>`, `Host` = the original host[:port],
  `extensions={"sni_hostname": host}`, so TLS still checks the certificate
  against the host name), try the next validated IP only on a connect error,
  never follow redirects and ignore proxy variables (`trust_env=False`).
- requests (pywebpush): `app/push/send.py` mounts `PinnedHTTPAdapter`, whose
  urllib3 connections connect to the validated addresses only; the session
  ignores proxy variables and follows no redirect (`max_redirects = 0`).
- `error_code(exc)` maps a failure to `timeout`, `tls_error`, `service_error`
  (HTTP status from the destination), `connect_error` or the `egress_*` code.

## Consumers

- System webhook (`app/notify.py`): checked at save (`PATCH /api/settings`, only
  when the URL or its flag changes) and before each send.
  `notification_destinations.webhook.allow_private_network`; a webhook stored
  before the guard (no such key) reads as allowed and
  `private_network_grandfathered` (`grandfather_legacy_webhook()` in
  `app/models.py`, applied on repository reads and backup restores).
- Web Push: `POST /api/push/subscribe` (400 coded) and every send
  (`app/push/send.py`), with the instance setting `push_allow_private_endpoints`.
- The test routes (`POST /push/subscriptions/{id}/test`,
  `POST /settings/notification-destinations/test`).
- Fanout (`app/routers/fanout.py`, `_check_destinations`): webhook `url`,
  Apprise `urls`, SQS `endpoint_url`, map upload `api_url`, each with
  `config.allow_private_network` (default false). Checked on create and on
  every PATCH that leaves the row enabled (a disabled row is saved without a
  DNS lookup; enabling it runs the check), then again by the module: before
  each send (webhook and map upload, pinned; Apprise) or at `start()` (SQS,
  boto3 is not pinned). Migration 100 sets `allow_private_network: true` and
  `private_network_grandfathered: true` on every row of those types that
  existed (one-time banner in the fanout list, "Got it" PATCHes the flag to
  false; `_carry_grandfathered_flag` also drops it when the destination or the
  opt-in changes). The grandfathered opt-in covers only the destination it was
  granted for: a PATCH that changes the destination while still sending
  `private_network_grandfathered: true` (the opt-in echoed, never confirmed)
  stores `allow_private_network: false`; the UI unticks the box when the
  destination changes and sends the flag as false when the user ticks it
  again. A backup restore (`prepare_fanout_config(restored=True)`)
  grandfathers a row with no `allow_private_network` key, and does no DNS
  lookup. An Apprise row whose URLs are refused here (a 4.18 file) is kept,
  disabled, with the warning `apprise_restored_disabled:<name>`
  (`prepare_refused_apprise_config`), as migration 100 keeps it.
- Planned: Store plugins, Links & Files (breaker / share service), Access
  (Web Push). New consumers call `validate_destination` and send through a
  pinned client; never add an outbound path to a user-chosen URL around it.

## Apprise (`apprise_urls.py`)

- The host of an Apprise URL is not `urlsplit().hostname`: each URL is
  instantiated (`Apprise.instantiate`) and the plugin is read.
  `ALLOWED_SCHEMES` classifies a scheme `user_host` (the plugin connects to
  `server.host`, or `smtp_host` for e-mail; that host goes through
  `validate_destination`) or `fixed_host` (only its service's API; nothing to
  resolve). `REFUSED_SCHEMES` lists every other plugin Apprise ships: refused
  with `egress_scheme` whatever the opt-in, **before** instantiation (some
  plugins touch the local machine when built). A native `https://` URL is
  classified by the plugin Apprise maps it to (Discord, Slack...).
- Modes: ntfy in cloud mode (`ntfy://topic`) and Matrix in t2bot mode reach a
  fixed host. `tgram://` without a chat id is refused
  (`apprise_chat_id_required`): Apprise would call `getUpdates`.
- Redirects: `server.redirects = False` on every instance after parsing (a
  `?redirect=yes` in the URL cannot win) plus `AppriseAsset.http_redirects =
  False`. The module sends the validated instances, never the raw URLs.
- Secondary hosts (`_disable_secondary_hosts`, after parsing): Matrix server
  discovery is forced off (`discovery=False`, cached discovery cleared).
  It is on by default for `matrixs` and would read
  `https://<host>/.well-known/matrix/client`, then send every request, the
  access token included, to the `base_url` it returns: any address. E-mail
  `wkd=yes` (PGP key lookup at `openpgpkey.<recipient domain>`) is forced off
  too. The other `user_host` plugins reach only `server.host` (Nextcloud's
  group lookup uses the same host). A plugin that follows a second URL must be
  added here, or refused.
- Not pinned: Apprise resolves and connects itself (requests, smtplib, paho).
  Residual risk, documented for users: DNS rebinding of a host the user typed.
- Errors: `AppriseUrlRefused(code, line, scheme)`, detail `{code, message,
  params: {line, context: "apprise", scheme}}`; the UI translates
  `errors.<code>_apprise` (i18next context) with a fallback to
  `errors.<code>`. Never the URL or a host.
- `redact_apprise_url()` keeps the scheme and, for `user_host`, the host;
  everything else is masked. The frontend `censorAppriseUrl` stays for display.
- `tests/test_apprise_urls.py` fails when the installed Apprise exposes a
  plugin in neither list: on an Apprise bump, read the new plugin and classify
  it (refused unless reviewed).

Tests: `tests/test_egress_guard.py`, `tests/test_apprise_urls.py`.
