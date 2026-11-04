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

## Known limits, not vulnerabilities

Meshloom is built for a trusted network, and some of its behaviour is
deliberate. These are documented and are not treated as vulnerabilities:

- **No user accounts.** Anyone who can reach the web interface controls the
  node. Pages of other sites are refused; scripts and tools that send no
  `Origin` header are not.
- **Bots run Python with no sandbox.** Anyone who can reach the interface at a
  local address (IP, `localhost`, `.local`, `.lan`, `.home.arpa`, `.internal`,
  the machine's name) can make the host run code while bots are enabled.
  Bots cannot be created or changed through any other name.
  `MESHCORE_DISABLE_BOTS=true` turns them off; the Linux package does so by
  default.
- **Basic auth is a single shared login.** It is an optional, coarse gate, and
  it sends the password in clear text unless you serve Meshloom over HTTPS.
- **The radio proxy has no authentication.** When turned on in
  **Settings > Proxy**, it listens on every interface, and Basic auth does not
  cover it. It closes any connection that does not speak its frame format.
- **Secrets are readable by anyone who can reach the interface.** The database
  backup in **Settings > Database** holds channel keys and integration
  settings. `GET /api/radio/private-key` returns the radio's private key when
  `MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT=true`.

They are described in [A trusted network](docs/user/en/trust.md) and
[Security](docs/user/en/deep/security.md). A way around one of the protections
that does exist (for example, bypassing Basic auth when it is set, sending a
request from another site, changing a bot through a public name, running a
bot while `MESHCORE_DISABLE_BOTS=true`, or getting the updater to install
something that is not a signed Meshloom release) is a vulnerability: please
report it.
