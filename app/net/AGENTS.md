# Egress guard and local addresses (`app/net/`)

One guard for every outbound request to a destination a user or a browser
chose (SSRF), and one definition of a local *client* address for the inbound
auth checks (`local_address.py`, at the end). Like every agent doc here, check
it against the code.

## Semantics (`egress_guard.py`)

- `validate_destination(url, *, allow_private_network=False, schemes={http, https}, allowed_hosts=None)`
  parses the URL, resolves A/AAAA (`loop.getaddrinfo`) and returns a
  `ResolvedDestination` (scheme, host, port, validated IPs), or raises
  `EgressBlocked(code)` **before any connection**. Codes are stable and
  translated by the UI under `errors.<code>`: `egress_scheme` (bad scheme,
  malformed URL or port), `egress_private_address`, `egress_unresolvable`,
  and for fixed-host callers `egress_host_not_allowed`,
  `egress_too_many_redirects`, `egress_request_too_large`,
  `egress_response_too_large` (see "Fixed-host callers"), and `egress_userinfo` (a
  URL with a user name or password: raised by `store_fetch` on the first URL and by
  `follow_checked_redirects` on any redirect target, MSHL-366).
  `egress_detail(code)` builds the coded HTTP detail (never a host, an IP or the URL).
  `EgressBlocked.always_refused` is true for a refusal no opt-in can lift:
  every `egress_scheme` from the URL check, and a metadata or documentation
  address or host. `permanent` is the subset that needs no DNS answer (the
  URL, an IP literal, `metadata.google.internal`); a forbidden address in a
  resolved answer is `always_refused` only, since a broken or hijacked
  resolver can return it once. Web Push deletes a stored subscription at once
  on `permanent`, and on `always_refused` only when a send at least
  `FORBIDDEN_ANSWER_CONFIRM_SECONDS` (1 h) after the first still meets it.
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

- httpx logs every request at INFO (`HTTP Request: GET <url> ...`). Importing
  the guard adds a filter to the `httpx` logger that drops the user info, query
  and fragment of that URL (API keys, webhook tokens), for every httpx client of
  the process.

- httpx: `pinned_async_client(dest, *, verify_tls=True, timeout)` yields a
  client whose `request()`/`post()` connect to a validated IP
  (`https://<ip>:<port><path>`, `Host` = the original host[:port],
  `extensions={"sni_hostname": host}`, so TLS still checks the certificate
  against the host name), try the next validated IP only on a connect error,
  never follow redirects and ignore proxy variables (`trust_env=False`).
- requests (pywebpush): `app/push/send.py` mounts `PinnedHTTPAdapter`, whose
  urllib3 connections connect to the validated addresses only (only
  `_new_conn` is replaced, so urllib3 v2 still sends SNI and checks the
  certificate against the URL host); the session ignores proxy variables and
  follows no redirect (`max_redirects = 0`).
- `error_code(exc)` maps a failure to `timeout`, `tls_error`, `service_error`
  (HTTP status from the destination), `connect_error` or the `egress_*` code.
- `PinnedClient.stream(method, ...)` is `request()` with the body left unread
  (an async context manager; the response is closed on exit), for callers
  that cap the body.

## Fixed-host callers (allowlist, redirects, caps)

For a caller that talks to a known set of hosts (the Store client, the plugin
`http` capability). The rules above are unchanged and still apply to every hop.

- `HostAllowlist.of("github.com", "objects.githubusercontent.com")` (or
  `.parse(entries)`): exact hosts, `host` or `host:port`, an IP literal
  allowed (`[v6]:port`, or a bare v6 without port), never a range. Entries are
  normalised like the URL host (lowercase, IDNA2008 A-label, no trailing dot,
  IP literal compressed). An entry without a port allows only the scheme's
  default port (`github.com` does not allow `github.com:8443`). `*`, a CIDR,
  a scheme, a path or user info raise `ValueError` when the list is built.
  The list does not restrict the scheme: a fixed-host caller must also pass
  `schemes=frozenset({"https"})`, or `http://github.com` is allowed too.
- `validate_destination(..., allowed_hosts=...)` refuses a host outside the
  list with `egress_host_not_allowed` **before the DNS lookup**; a listed host
  still meets the address rules (private refused unless opted in, metadata
  and documentation always).
- `follow_checked_redirects(method, url, *, timeout, allowed_hosts=None,
  allow_private_network=False, schemes=..., max_redirects=0,
  max_request_bytes=None, max_response_bytes=None, total_timeout=None,
  content=None, headers=None, header_filter=None, cross_origin_headers=(),
  verify_tls=True, truncate_response=False)` returns a
  `CheckedResponse` (status, headers, body, last hop URL, redirect count,
  `truncated`):
  - every hop, the first included, goes through `validate_destination` with
    the same allowlist, schemes and private rule, then a fresh pinned client
    (no cookie jar from one hop to the next). `Location` is joined to the URL
    the caller asked for, never the pinned IP URL; the fragment is dropped. A redirect
    target with user info is refused (`egress_userinfo`).
    303 (and 301/302 after a POST) becomes a GET without body or
    `Content-Type`; 307/308 keep both. Once a hop leaves the origin (scheme,
    host, port), only `CROSS_ORIGIN_HEADERS` (`Accept`, `User-Agent`,
    `Accept-Encoding`, `Content-Type`) and the names the caller lists in
    `cross_origin_headers` follow it: `Authorization`, `X-Api-Key`,
    `PRIVATE-TOKEN` and any other header are dropped.
  - `max_redirects=0` (default) returns a redirect as is; one more redirect
    than allowed raises `egress_too_many_redirects`.
  - `content` is bytes or a `StreamedBody(length, open)`: `open()` gives a fresh
    async iterator for each attempt (pinned IP, redirect hop) and the guard sets
    `Content-Length` to `length` itself, so the body is never sent chunked (h11
    refuses a body that does not match it). The share upload uses it to stream a
    staged file.
  - a body above `max_request_bytes` is refused before any DNS lookup
    (`egress_request_too_large`). The response is refused unread when its
    `Content-Length` is above `max_response_bytes`, and aborted (connection
    closed) as soon as the streamed body passes it
    (`egress_response_too_large`). With `truncate_response=True` (the plugin
    `http` capability) the body is cut at `max_response_bytes` instead, the rest
    left unread, `truncated` set, and a larger `Content-Length` is not refused.
    The body is read raw, never decoded: the
    request always sends `Accept-Encoding: identity` (set after the header
    filter, a caller's own value is replaced) and an answer with any other
    `Content-Encoding` is refused unread (`egress_response_encoding`), so a
    small compressed body cannot inflate past the cap. A HEAD, 204 or 304
    answer has no body: its `Content-Length` is not checked.
  - `timeout` is httpx's per-operation timeout; `total_timeout` (seconds)
    bounds the whole chain, so a server dripping bytes cannot hold the call
    (raises `TimeoutError`, `error_code` gives `timeout`).
  - caller headers pass `filter_request_headers`: `DROPPED_REQUEST_HEADERS`
    (`Host`, `Content-Length`, `Transfer-Encoding`, `Cookie`, and the
    hop-by-hop `Connection`, `Keep-Alive`, `Proxy-Authorization`,
    `Proxy-Connection`, `TE`, `Trailer`, `Upgrade`) never reach the transport;
    `header_filter(name, value)` may drop more, never restore one.
- `lan_hosts=HostAllowlist` (on `validate_destination` and
  `follow_checked_redirects`; the plugin `http` capability's declared LAN
  devices, MSHL-282): a host in that list may use `http` or `https` whatever
  `schemes` says, and every resolved address must pass `check_lan_addresses`
  (`is_lan_address`: `10/8`, `172.16/12`, `192.168/16`, `100.64/10` (CGNAT,
  Tailscale), `fc00::/7`, tested as written, so mapped and transition forms are
  not LAN; never a metadata address). Anything else raises `egress_lan_address`,
  metadata and documentation `egress_private_address`; an address of the
  Meshloom host itself (`is_self_address`: bind probe, plus the default gateways
  inside a bridged container, MSHL-330; with host networking only the Home Assistant
  supervisor gateway, MSHL-336; host networking needs the engine's interfaces and
  default routes through interfaces that are their own link, so Docker-in-Docker
  stays bridged, MSHL-340) raises `egress_self_address`. In the Home Assistant
  add-on only (`MESHLOOM_INSTALL_KIND=addon`, declared by `meshloom/run.py`,
  never inferred from addresses, so a deb/Docker/source install whose LAN is
  numbered from `172.30.32.0/23` is unaffected), any address of the supervisor
  network (`172.30.32.0/23`, `fd0c:ac1e:2100::/48`: the Supervisor, the other
  add-ons; `is_supervisor_network_address`, mapped spellings unwrapped) raises
  `egress_supervisor_address` (MSHL-350). A host in neither
  list raises `egress_host_not_allowed` before the scheme check and the lookup.
  `follow_checked_redirects(..., lan_verify_tls=False)` turns the certificate
  check off only on the hops that reach a `lan_hosts` host (self-signed LAN
  device, MSHL-331). Without `lan_hosts` nothing changes.
- The existing consumers below keep `validate_destination` plus
  `pinned_async_client` and never follow redirects; they did not change.

## Consumers

- System webhook (`app/notify.py`): checked at save (`PATCH /api/settings`, only
  when the URL or its flag changes) and before each send.
  `notification_destinations.webhook.allow_private_network`; a webhook stored
  before the guard (no such key) reads as allowed and
  `private_network_grandfathered` (`grandfather_legacy_webhook()` in
  `app/models.py`, applied on repository reads and backup restores).
- Apprise notification medium (`app/notifications/apprise_medium.py`):
  `notification_destinations.apprise.urls` through `validate_apprise_urls`
  with `apprise.allow_private_network`, at save (`PATCH /api/settings`, only
  when the URLs or the flag change) and before each send, URL by URL.
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
  A restored row whose opt-in is on (`allow_private_network: true`, or a
  legacy row the restore grandfathers) comes back disabled with the warning
  `private_network_restored_disabled:<name>`: the user re-enables it, which
  runs the check.
- Share service client (`app/services/share.py`, `app/AGENTS.md`, "Share
  service client"): `follow_checked_redirects` with `HostAllowlist.of(SHARE_HOST)`,
  HTTPS only, no redirect, capped answers, only while `share_enabled`.
- Store egress (`app/plugins/store_egress.py`, `app/AGENTS.md`, "Store egress"):
  `store_fetch()` sends through `follow_checked_redirects` with `STORE_ALLOWLIST`
  (`github.com`, the exact release-asset redirect hosts `objects.githubusercontent.com`
  and `release-assets.githubusercontent.com`, and `store.meshloom.app`), HTTPS only,
  only while `app_settings.store_online`; `StoreOffline` otherwise, before any lookup.
  It refuses a URL with user info (`egress_userinfo`) and narrows a caller's
  `allowed_hosts` to `STORE_ALLOWLIST`. Plugin archive downloads use it (MSHL-366), and
  so do the custom repository lookups (`app/plugins/custom_repos.py`, MSHL-196: GitHub
  hosts only, `store.meshloom.app` excluded).
- The plugin `http` capability (the
  plugin's declared hosts, plus its declared `lan_hosts`, 10 s, 1 MB response, 256 KB body, redirects
  re-validated to a declared host) both send through
  `follow_checked_redirects` with a `HostAllowlist`.
  New consumers call
  `validate_destination` and send through a pinned client (or
  `follow_checked_redirects`); never add an outbound path to a user-chosen URL
  around it.

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
  everything else is masked. It feeds `urls_redacted` in the fanout API (the
  stored `urls` read `********`, `app/secret_fields.py`); the frontend
  `censorAppriseUrl` is only a fallback for a config without it. Its
  `user_host` branch is `redact_url()`, which also gives the webhook
  `url_redacted` (fanout and notification); `url_secret_parts()` (the user,
  path and query parts) is the matching scrub list for error texts.
- `tests/test_apprise_urls.py` fails when the installed Apprise exposes a
  plugin in neither list: on an Apprise bump, read the new plugin and classify
  it (refused unless reviewed).

## Circuit breaker (`breaker.py`)

`CircuitBreaker(threshold=, open_seconds=, clock=time.monotonic)`: closed until
`threshold` consecutive `record_failure()`, then open (`allow()` false,
`cooling()` true) for `open_seconds`, then half-open (`allow()` grants exactly
one trial). `record_success()` closes it; `release_trial()` gives back a trial
that reached no verdict (cancellation). In memory. Used by the Community client
(`CommunityBreaker`, 3 failures, 30 s) and the share client (3 failures, 5 min),
each with its own instance; the caller decides what counts as a failure.

Tests: `tests/test_egress_guard.py`, `tests/test_egress_guard_checked_fetch.py`
(allowlist, redirect chains, caps, headers), `tests/test_apprise_urls.py`,
`tests/test_push_send_egress.py` (Web Push: TLS SNI to the pinned IP, lazy deletion).

## Local client addresses (`local_address.py`, MSHL-431)

Inbound only: whether a request's client is local (this host or the household
network) or public. Not an egress question: destinations go through the guard
above, and `pairing.is_lan_host`, `backup.endpoint_looks_private` and
`channels.email_is_cloud` keep their own heuristics.

- `LOCAL_NETWORKS`: `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`, `169.254.0.0/16`, `100.64.0.0/10` (CGNAT stays local,
  MSHL-434), `::1/128`, `fc00::/7`, `fe80::/10`. Everything else is public,
  documentation, benchmarking, reserved, unspecified, multicast, NAT64, 6to4,
  Teredo, `fec0::/10` and IPv4-compatible included: a LAN never hands those
  out, so every caller takes its safe side.
- `parse_address()` is strict (`ipaddress.ip_address(raw)`, no strip, no
  split): whitespace, a `%` on IPv4 or IPv4-mapped, an empty zone are not
  addresses; a plain IPv6 zone id is dropped after parsing; IPv4-mapped is
  unmapped. A trusted proxy's `X-Forwarded-For` reaches `scope["client"]`
  verbatim, so trimming would turn `127.0.0.1%x` local. `is_local()` /
  `is_public()` take text or an address object, and anything that is not an
  address is public (fail closed); `network_is_local()` for a rule's CIDR.
- Callers: `ratelimit.client_is_public` (login panel hint, `setup_code`
  exposure), `fail_log` (`proxied`), `ip_ban.is_local`/`bannable` (adds the
  host's on-link global IPv6 prefixes), `trusted_network.rule_blocker` (via
  `ip_ban.is_local`) and `public_or_wide`. Listed range by range, never
  `is_private`/`is_global` (they differ between Python versions). Tests:
  `tests/test_local_address.py`.
