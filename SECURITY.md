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
boundary: a bubblewrap sandbox on Linux, a dedicated uid per plugin
instance in the Docker image and the Home Assistant add-on
(`docs/dev/plugins-security.md`, "OS isolation"). **A plugin that escapes that
boundary is a vulnerability**: reading the database, the server's environment
or memory, another plugin's memory, writing Meshloom's files, reaching the network under bubblewrap, or
outliving its kill under bubblewrap. Report it as above. A bypass of the
in-process guards alone (the audit hook, the import allowlist) is not one: they
are documented as defence in depth, not as the boundary. The "hooks only" level
(no bubblewrap and no dedicated account) is a known limit, logged at every
start and reported in `/api/health`; so are the limits listed in that document
for the dedicated account (plugins share one uid, so they can read each
other's work directories and signal each other, but not each other's memory;
the network, loopback and the unauthenticated radio proxy included, is not
cut).

## Known limits, not vulnerabilities

Meshloom is built for a network whose users you know, and some of its behaviour
is deliberate. These are documented and are not treated as vulnerabilities:

- **Accounts are keys, not walls.** Every `/api` route and the WebSocket need a
  signed-in account and declare the role that may call them (`admin`, `member`,
  `viewer`, `kiosk`; `docs/dev/auth.md`). Whoever holds an account has its
  powers: an `admin` can read all history, send as the node, write extensions and
  change every setting, and a `member` can send as the node. A trusted-network
  rule, which an administrator can add, signs a device in without a login, and
  the rule is closed behind any proxy, tunnel, ingress or relay. A Basic
  `Authorization` header with an account's name and password is still accepted
  until 5.2 (short pauses after a failure; never for an account with a second
  factor). Wrong
  passwords slow sign-in down; they never lock an account.
- **Other web pages are kept out, with documented gaps.** CORS is same-origin
  plus the origins listed in **Settings > Local Configuration > Allowed
  origins** (empty by default; a listed site can do what the signed-in user
  can, WebSocket included), and `POST`, `PUT`, `PATCH` and `DELETE` from another
  site are refused (`auth_origin_rejected`), with or without a session. Requests
  that carry neither `Origin` nor `Sec-Fetch-Site` (curl, scripts, sensors) are
  not browsers and pass that rule; they still need an account or a token. The
  WebSocket refuses an upgrade whose `Origin` neither matches `Host` (or the
  first `X-Forwarded-Host`) nor is listed; one without `Origin` is accepted if
  authenticated. CORS and the WebSocket share one origin policy
  (`app/origin_policy.py`). `X-Forwarded-Host` counts only for the WebSocket's
  same-origin match, and is trusted from any client: a browser page cannot set
  it. DNS rebinding: the `Host` header is checked in observation mode in 5.0.0
  (an unknown name is logged and let through, so a reverse proxy name keeps
  working; refused by default in 5.1), and always strictly before a trusted
  network signs anyone in.
- **Share writes accept only same-origin browser requests.** `POST /api/share/links`
  (and every later write under `/api/share`) answers 403 `share_cross_site` when
  the browser sends a `Sec-Fetch-Site` other than `same-origin` or `none`
  (`same-site` included). A request without the header
  (curl, scripts) passes; the route's permission (`send`) still applies. Defence in
  depth on top of the auth gate.
- **Extensions run administrator-approved Python.** Each runs in a process of its
  own with only the permissions an administrator accepted, behind the walls of the
  isolation level in force (`bwrap`, `setuid`, or `none`, where a hostile extension
  could read Meshloom's files; `docs/user/en/deep/plugins.md`). Any administrator can
  therefore get code running on the host. `MESHCORE_DISABLE_PLUGINS=true` locks the
  whole extension system. The Python bots of 4.18 and earlier, which ran with no
  sandbox, became extensions in 5.0.0, switched off until reviewed.
- **Over plain HTTP the password and the session cookie travel in clear text.**
  Serve Meshloom over HTTPS beyond a network you know. Passkeys are offered only
  where the browser allows them (HTTPS on a declared name, or `localhost`).
- **The radio proxy has no authentication, by owner decision.** When turned on
  in **Settings > Proxy**, it listens on every interface (port 5051 by default,
  `0.0.0.0`), accounts do not cover it, and anyone who can reach the port can
  use the radio and send as the node. The apps that speak the protocol cannot
  sign in. Do not forward the port; keep it on a network you know.
- **Home Assistant and its add-ons trust each other.** Under ingress, Meshloom
  accepts the user the Supervisor names (only from the Supervisor's address,
  never from a published port), and the add-on shares the host's D-Bus with
  root rights. Do not run an add-on you would not trust with Home Assistant.
- **The radio's private key is readable by any administrator**
  (`GET /api/radio/private-key`, the full backup in **Settings > Database**,
  which can also carry password hashes and two-factor secrets when **Include
  accounts** is checked). A listed allowed origin can read them too. The backup
  file can be encrypted with a passphrase.

They are described in [A trusted network](docs/user/en/trust.md),
[Security](docs/user/en/deep/security.md) and
[Accounts and access](docs/user/en/deep/access.md). A way around one of the
protections that does exist (for example, reaching a route above the role it
declares, signing in without a valid credential, getting a trusted-network rule
to apply through a proxy, starting an extension while `MESHCORE_DISABLE_PLUGINS=true`, or
getting the updater to install something that is not a signed Meshloom release)
is a vulnerability: please report it.
