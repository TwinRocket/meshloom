"""What counts as a local (non-public) client address: one definition for the auth gate (MSHL-431).

A request's **client** address is either *local* (it can only come from this
host or the household network) or *public* (anything else). Every inbound
decision that depends on it asks this module:

- the login limiter's "public client" hint and the exposed first run
  (``app/auth/ratelimit.client_is_public``, ``app/auth/setup_code.request_exposed``);
- the built-in ban's exclusions, through ``is_ban_exempt`` (``app/auth/ip_ban.bannable``,
  ``check``), and the ``AUTH_FAIL`` line's ``proxied`` field, through the same;
- the login-free-on-the-local-network gate and its notice
  (``app/auth/providers/trusted_network.py``: ``rule_blocker``, ``public_or_wide``).

Outbound destinations are a different question (SSRF): the egress guard
(``app/net/egress_guard.py``) classifies them, not this module. So do the
pairing LAN candidate (``app/auth/pairing.is_lan_host``: a URL other devices
must reach, never loopback or CGNAT) and the push-endpoint and mail-host
heuristics (``app/services/backup.endpoint_looks_private``,
``app/notifications/channels.email_is_cloud``).

**Local** is exactly ``LOCAL_NETWORKS``:

- loopback: ``127.0.0.0/8``, ``::1/128``;
- private: ``10.0.0.0/8``, ``172.16.0.0/12``, ``192.168.0.0/16`` (RFC 1918),
  ``fc00::/7`` (unique local, ULA);
- link-local: ``169.254.0.0/16``, ``fe80::/10``;
- shared address space ``100.64.0.0/10`` (carrier-grade NAT, Tailscale).

**Ban-exempt** (``is_ban_exempt``, MSHL-434) is local minus the shared address
space: a tailnet device or a CGNAT neighbour is a distinct remote client, so
its failed sign-ins are counted and it can be banned, while the LAN, loopback,
link-local and ULA never are. Its other callers still read CGNAT as local
(the limiter hint, the exposed first run, the login-free gate).

Everything else is **public**, deliberately including ranges that are not
routable either: the documentation ranges (``192.0.2.0/24``,
``198.51.100.0/24``, ``203.0.113.0/24``, ``2001:db8::/32``, ``3fff::/20``),
benchmarking (``198.18.0.0/15``, ``2001:2::/48``), ``192.0.0.0/24``,
``0.0.0.0/8`` and ``::`` (unspecified), ``240.0.0.0/4``, multicast, NAT64
(``64:ff9b::/96``), 6to4 (``2002::/16``), Teredo (``2001::/32``), the
deprecated site-local ``fec0::/10`` and IPv4-compatible ``::a.b.c.d``. A LAN
never hands those out, so a peer in one of them is unexpected, and every
caller then takes its safe side: the limiter and the ban count it,
``proxied`` stays ``0``, the login-free gate refuses, a first run asks for
the installation code.

Rules that hold for every caller:

- An IPv4-mapped IPv6 address (``::ffff:a.b.c.d``) is judged as its IPv4
  address (a dual-stack socket reports IPv4 clients that way).
- A value that is not exactly an IP address (absent, empty, a name, garbage,
  surrounding whitespace, a ``%`` on an IPv4 or IPv4-mapped address) is
  public (fail closed): a trusted proxy's ``X-Forwarded-For`` value reaches
  ``scope["client"]`` verbatim, so ``127.0.0.1%x`` or `` 10.0.0.1`` must never
  read as local.
- A zone id on a plain IPv6 address (``fe80::1%eth0``) is dropped after
  ``ipaddress`` accepted it: the address before the ``%`` decides.
- Listed range by range, never ``ipaddress.is_private`` / ``is_global``: those
  changed between Python 3.11 and 3.13 and cover the documentation ranges.

Not part of this definition: the on-link global IPv6 prefixes of the host,
which only the ban and the login-free gate add (``app/auth/ip_ban.is_local``).
"""

from __future__ import annotations

import ipaddress

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

LOCAL_NETWORKS: tuple[IPNetwork, ...] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "100.64.0.0/10",  # SHARED_ADDRESS_SPACE
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)

# Carrier-grade NAT and Tailscale: local, but not exempt from the ban (MSHL-434).
SHARED_ADDRESS_SPACE = ipaddress.IPv4Network("100.64.0.0/10")


def parse_address(raw: str | None) -> IPAddress | None:
    """``raw`` as an address, IPv4-mapped IPv6 as IPv4, or None when it is not one.

    Strict: no whitespace is stripped and ``ipaddress`` alone decides (it
    refuses a ``%`` on IPv4 and an empty zone). A zone id on a plain IPv6
    address is dropped (``fe80::1%eth0`` gives ``fe80::1``), so the result never
    carries request text; on an IPv4-mapped address it is refused."""
    if not raw:
        return None
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return None if address.scope_id is not None else address.ipv4_mapped
        if address.scope_id is not None:
            address = ipaddress.IPv6Address(int(address))
    return address


def _address(value: IPAddress | str | None) -> IPAddress | None:
    if isinstance(value, ipaddress.IPv4Address | ipaddress.IPv6Address):
        return parse_address(str(value))
    return parse_address(value)


def is_local(value: IPAddress | str | None) -> bool:
    """Whether the address is in ``LOCAL_NETWORKS`` (module docstring). Not an address: False."""
    address = _address(value)
    if address is None:
        return False
    return any(
        address.version == network.version and address in network for network in LOCAL_NETWORKS
    )


def is_ban_exempt(value: IPAddress | str | None) -> bool:
    """Whether the built-in ban must never count ``value``: local, CGNAT excepted
    (module docstring). Not an address: False."""
    address = _address(value)
    if address is None or not is_local(address):
        return False
    return not (address.version == 4 and address in SHARED_ADDRESS_SPACE)


def is_public(value: IPAddress | str | None) -> bool:
    """The opposite of ``is_local``: anything that is not a local address, garbage included."""
    return not is_local(value)


def network_is_local(network: IPNetwork) -> bool:
    """Whether every address of ``network`` is local (it fits in one of ``LOCAL_NETWORKS``)."""
    return any(
        network.version == local.version and network.subnet_of(local)  # type: ignore[arg-type]
        for local in LOCAL_NETWORKS
    )
