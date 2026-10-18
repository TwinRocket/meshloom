# Security policy

## Reporting a vulnerability

Report it privately, through GitHub: open the **Security** tab of this
repository and click **Report a vulnerability**
(<https://github.com/TwinRocket/meshloom/security/advisories/new>).
Only the maintainers see the report.

Do not open a public issue, pull request or discussion for a vulnerability.

A useful report says which version and install method you use (package,
Docker, Home Assistant add-on, Raspberry Pi image, source), what an attacker
needs (network access, a login, a click), and how to reproduce it.

## What to expect

Meshloom is maintained on a best-effort basis. As a guide, not a commitment:

- an acknowledgement within 7 days;
- a first assessment, or questions back, after that;
- a fix in a release, then a published advisory crediting you, unless you
  prefer otherwise.

## Supported versions

Only the latest release receives security fixes. Older versions are not
patched: update to the latest release (see [Update](README.md#update)).

## Plugin isolation

Plugins (Meshloom 5) run in a child process behind an operating-system
boundary: a bubblewrap sandbox on Linux, a dedicated `meshloom-plugin`
account in the Docker image and the Home Assistant add-on
(`docs/dev/plugins-security.md`, "OS isolation"). **A plugin that escapes that
boundary is a vulnerability**: reading the database, the server's environment
or memory, writing Meshloom's files, reaching the network under bubblewrap, or
outliving its kill under bubblewrap. Report it as above. A bypass of the
in-process guards alone (the audit hook, the import allowlist) is not one: they
are documented as defence in depth, not as the boundary. The "hooks only" level
(no bubblewrap and no dedicated account) is a known limit, logged at every
start and reported in `/api/health`; so are the limits listed in that document
for the dedicated account (shared uid between plugins, network not cut).

## Known limits, not vulnerabilities

Meshloom is built for a trusted network, and some of its behaviour is
deliberate. These are documented and are not treated as vulnerabilities:

- **No user accounts.** Anyone who can reach the web interface controls the
- **No user accounts.** Anyone who can reach the web interface controls the
  node. The WebSocket (`/api/ws`) is the exception to cross-origin access: an
  upgrade whose `Origin` neither matches `Host` (or the first
  `X-Forwarded-Host`) nor is in the allowed-origins list is refused with 403
  before it is accepted; one without `Origin` (scripts, native clients) is
  accepted.
- **Other web pages are only partly kept out.** CORS is same-origin, plus the
  origins listed in **Settings > Local Configuration > Allowed origins** (empty
  by default), so another site cannot read API answers or send JSON requests.
  It can still send requests that need no preflight (a `POST` with no body)
  without reading the answer. Nothing checks `Host` for the HTTP API either
  (DNS rebinding).
  CORS and the WebSocket share one origin policy (`app/origin_policy.py`): the
  same list and the same parsing (lowercase, punycode host, default port
  dropped), so a listed site gets both the HTTP API and `/api/ws`.
  `X-Forwarded-Host` counts only for the WebSocket's same-origin match, and is
  trusted from any client: a browser page cannot set it, but a non-browser
  client sends no `Origin` and is accepted anyway. The stored list is
  re-validated when read: an entry written outside the API (SQL, a restore)
  that is a wildcard, `null`, or not a plain `http(s)` origin is ignored and
  logged.
- **Share writes refuse requests marked cross-site.** On the 5.0 integration
  branch, `POST /api/share/links` (and every later write under `/api/share`)
  answers 403 `share_cross_site` when the browser sends `Sec-Fetch-Site:
  cross-site`, so a page open on the LAN cannot publish under the instance
  identity. A request without the header (curl, bots) passes; the route's
  permission (`send`) still applies. It is defence in depth on top of the auth
  gate and does not touch CORS.
- **Bots run Python with no sandbox.** Anyone who can reach the interface can
  make the host run code while bots are enabled. `MESHCORE_DISABLE_BOTS=true`
  turns them off; the Linux package does so by default.
- **Basic auth is a single shared login.** It is an optional, coarse gate, and
  it sends the password in clear text unless you serve Meshloom over HTTPS.
  On the 5.0 integration branch (`preversion-5`) login is mandatory: every
  `/api` route needs an account, the Basic credentials are imported once as the
  administrator, and unsafe requests from another site are refused
  (`docs/dev/auth.md`). Over plain HTTP the session cookie and the password
  still travel in clear text.
- **The radio proxy has no authentication.** When turned on in
  **Settings > Proxy**, it listens on every interface, and Basic auth does not
  cover it.
- **The radio's private key is readable by anyone who can reach the
  interface** (`GET /api/radio/private-key`, the full backup in
  **Settings > Database**). A listed allowed origin can read them too. The
  backup file can be encrypted with a passphrase.

They are described in [A trusted network](docs/user/en/trust.md) and
[Security](docs/user/en/deep/security.md). A way around one of the protections
that does exist (for example, bypassing Basic auth when it is set, running a
bot while `MESHCORE_DISABLE_BOTS=true`, or getting the updater to install
something that is not a signed Meshloom release) is a vulnerability: please
report it.
