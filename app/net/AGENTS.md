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
  NAT64 (`64:ff9b::/96`, `64:ff9b:1::/48`), 6to4 `2002::/16`, Teredo
  `2001::/32`, IPv4-compatible `::/96`, and IPv4-mapped IPv6 of those. The
  opt-in **covers loopback** (Gotify or Home Assistant on the same host).
- Refused whatever the flag: `169.254.169.254`, `fd00:ec2::254`,
  `100.100.100.200` (also mapped or behind NAT64) and `metadata.google.internal`.
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
- Planned: fanout webhook, Apprise, SQS, map upload, Store plugins, Links &
  Files (breaker / share service), Access (Web Push). New consumers call
  `validate_destination` and send through a pinned client; never add an
  outbound path to a user-chosen URL around it.

Tests: `tests/test_egress_guard.py`.
