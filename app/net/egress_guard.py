"""The one egress guard for user-configured destinations (SSRF).

Every outbound request to a URL a user or a browser chose (system webhook, Web
Push endpoints, the notification test routes, the fanout webhook, Apprise
(``app/net/apprise_urls.py``), SQS and map upload; later Store plugins and
Links) goes through ``validate_destination`` before any connection, then
connects to one of the validated IPs (``pinned_async_client`` for httpx, the
pinned urllib3 connections in ``app/push/send.py`` for pywebpush), so the DNS
answer cannot change between the check and the send.

Callers that talk to a fixed set of hosts (the Store client, the plugin
``http`` capability) also pass a ``HostAllowlist`` and send through
``follow_checked_redirects``: every redirect hop is validated again, the
request and response bodies are capped (the response while it streams) and
headers a caller must never set are dropped. The plugin capability also passes
its declared LAN devices as ``lan_hosts``: those, and only those, may be reached
over plain HTTP and must resolve to a LAN address (10/8, 172.16/12, 192.168/16,
100.64/10, fc00::/7) that is not the Meshloom host itself (``is_self_address``)
nor, in the Home Assistant add-on, the supervisor network
(``is_supervisor_network_address``), checked on every hop.

Address classes are tested range by range. ``ipaddress.is_private`` and
``is_global`` changed between Python 3.11 and 3.13 (IPv4-mapped IPv6,
100.64.0.0/10), so they are never used. See ``app/net/AGENTS.md``.
"""

from __future__ import annotations

import asyncio
import errno
import ipaddress
import logging
import os
import re
import socket
import ssl
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

import httpx

from app.services.install_kind import detect_install_kind

logger = logging.getLogger(__name__)


class _HttpxUrlRedaction(logging.Filter):
    """httpx logs every request URL at INFO (``HTTP Request: GET <url> ...``):
    keep its scheme, host, port and path, never its user info, query or
    fragment, where API keys and webhook tokens sit."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                arg.copy_with(username=None, password=None, query=None, fragment=None)
                if isinstance(arg, httpx.URL)
                else arg
                for arg in record.args
            )
        return True


# Once per process, at import: every guarded request goes through httpx here.
logging.getLogger("httpx").addFilter(_HttpxUrlRedaction())

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
EgressCode = Literal[
    "egress_scheme",
    "egress_private_address",
    "egress_unresolvable",
    "egress_host_not_allowed",
    "egress_too_many_redirects",
    "egress_request_too_large",
    "egress_response_too_large",
    "egress_response_encoding",
    "egress_lan_address",
    "egress_self_address",
    "egress_supervisor_address",
    "egress_userinfo",
]

DEFAULT_SCHEMES = frozenset({"http", "https"})

# Refused unless the destination allows a private network. Loopback is part of
# the opt-in: Gotify or ntfy on the same host are legitimate targets.
_PRIVATE_V4 = tuple(
    ipaddress.IPv4Network(n)
    for n in (
        "0.0.0.0/8",  # "this network"; 0.0.0.0 reaches the local host on Linux
        "10.0.0.0/8",
        "100.64.0.0/10",  # CGNAT
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",  # IETF protocol assignments
        "192.168.0.0/16",
        "198.18.0.0/15",  # benchmarking; also the fake-IP pool of local DNS proxies
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, broadcast included
    )
)
_PRIVATE_V6 = tuple(
    ipaddress.IPv6Network(n)
    for n in (
        "::/128",
        "::1/128",
        "fe80::/10",
        "fec0::/10",  # deprecated site-local
        "fc00::/7",
        "ff00::/8",
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",  # local-use NAT64
        "2002::/16",  # 6to4
        "2001::/32",  # Teredo
        "::/96",  # deprecated IPv4-compatible
    )
)
# Refused whatever the flag: documentation ranges never belong to a real host.
_DOCUMENTATION_V4 = tuple(
    ipaddress.IPv4Network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
_DOCUMENTATION_V6 = (ipaddress.IPv6Network("2001:db8::/32"),)

_IPV4_MAPPED = ipaddress.IPv6Network("::ffff:0:0/96")
# IPv6 forms that carry an IPv4 address in their low 32 bits.
_EMBEDS_V4_LOW = tuple(
    ipaddress.IPv6Network(n)
    for n in (
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",  # local-use NAT64 (the /96 layout)
        "::/96",  # deprecated IPv4-compatible
    )
)
_6TO4 = ipaddress.IPv6Network("2002::/16")
_TEREDO = ipaddress.IPv6Network("2001::/32")

# Refused whatever the flag: cloud instance metadata services.
_METADATA_IPS = frozenset(
    ipaddress.ip_address(a)
    for a in (
        "169.254.169.254",  # AWS, GCP, Azure, OpenStack...
        "fd00:ec2::254",  # AWS IPv6
        "100.100.100.200",  # Alibaba Cloud
    )
)
_METADATA_HOSTS = frozenset({"metadata.google.internal"})

# What a declared LAN host (`lan_hosts`) must resolve to: the home and office network
# ranges and 100.64.0.0/10, where a VPN such as Tailscale numbers the user's own
# devices (MSHL-331); nothing else (no loopback, link-local, transition form or public IP).
_LAN_V4 = tuple(
    ipaddress.IPv4Network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
)
_LAN_V6 = (ipaddress.IPv6Network("fc00::/7"),)


# English fallbacks for API details; the UI translates errors.<code>.
EGRESS_MESSAGES: dict[str, str] = {
    "verify_tls_requires_private_destination": "Certificate checks can only be turned off "
    "for a destination on a private network (LAN, localhost)",
    "smtp_plaintext_auth_refused": "A login and password are never sent without encryption "
    "to a mail server outside your private network",
    "egress_scheme": "The address must be an http:// or https:// URL",
    "egress_private_address": "The address is on a private network (LAN, localhost); "
    "allow private networks for this destination to use it",
    "egress_unresolvable": "The host name was not found; try typing the IP address instead",
    "egress_host_not_allowed": "This host is not in the list of hosts allowed here",
    "egress_too_many_redirects": "The destination redirected too many times",
    "egress_request_too_large": "The request body is larger than allowed",
    "egress_response_too_large": "The answer is larger than allowed",
    "egress_response_encoding": "The answer is compressed, which is not allowed here",
    "egress_lan_address": "This local-network host did not resolve to an address of your "
    "local network",
    "egress_self_address": "This local-network host is the machine Meshloom runs on, which "
    "a plugin may not reach",
    "egress_supervisor_address": "This address is on the Home Assistant Supervisor's internal "
    "network (Supervisor, other add-ons), which a plugin may not reach",
    "egress_userinfo": "The address must not contain a user name or password",
}


def egress_detail(code: str) -> dict[str, str]:
    """A coded HTTP error detail for a refusal: never the host, the IP or the URL."""
    return {"code": code, "message": EGRESS_MESSAGES.get(code, "Destination refused")}


class EgressBlocked(Exception):
    """A destination the guard refuses. ``code`` is stable and translated by the UI.

    ``always_refused`` marks a refusal no setting can lift (a bad scheme or
    URL, a metadata or documentation address): the opt-in would not change it.
    ``permanent`` is the subset that does not depend on a DNS answer (the URL,
    an IP literal, a metadata host name): the same URL is refused again
    whatever the resolver says. A forbidden address in a DNS answer is
    ``always_refused`` but not ``permanent``: a broken or hijacked resolver can
    give it once.
    """

    def __init__(
        self, code: EgressCode, *, permanent: bool = False, always_refused: bool = False
    ) -> None:
        super().__init__(code)
        self.code: EgressCode = code
        self.permanent = permanent
        self.always_refused = always_refused or permanent


@dataclass(frozen=True)
class ResolvedDestination:
    """A validated URL and the addresses it may be reached at."""

    url: str
    scheme: str
    host: str
    port: int
    ips: tuple[IPAddress, ...]


def _unmapped(ip: IPAddress) -> IPAddress:
    if isinstance(ip, ipaddress.IPv6Address) and ip in _IPV4_MAPPED:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


def _embedded_v4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 an IPv6 transition form routes to: NAT64, IPv4-compatible, 6to4, Teredo."""
    if any(ip in net for net in _EMBEDS_V4_LOW):
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip in _6TO4:
        return ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
    if ip in _TEREDO:
        # The client address is stored inverted (RFC 4380).
        return ipaddress.IPv4Address(~int(ip) & 0xFFFFFFFF)
    return None


def _forms(ip: IPAddress) -> tuple[IPAddress, ...]:
    """The address itself and, for an IPv6 transition form, the IPv4 inside it."""
    ip = _unmapped(ip)
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = _embedded_v4(ip)
        if embedded is not None:
            return (ip, embedded)
    return (ip,)


def is_metadata_address(ip: IPAddress) -> bool:
    return any(form in _METADATA_IPS for form in _forms(ip))


def is_documentation_address(ip: IPAddress) -> bool:
    for form in _forms(ip):
        nets = _DOCUMENTATION_V4 if isinstance(form, ipaddress.IPv4Address) else _DOCUMENTATION_V6
        if any(form in net for net in nets):
            return True
    return False


def is_private_address(ip: IPAddress) -> bool:
    """True for every address class the guard refuses without the opt-in."""
    ip = _unmapped(ip)
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in net for net in _PRIVATE_V4)
    return any(ip in net for net in _PRIVATE_V6)


def is_lan_address(ip: IPAddress) -> bool:
    """True only for a plain LAN address: 10/8, 172.16/12, 192.168/16, 100.64/10 or fc00::/7, written
    as itself (an IPv4-mapped or transition form is not), never a metadata address."""
    if is_metadata_address(ip):
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in net for net in _LAN_V4)
    return any(ip in net for net in _LAN_V6)


def _binds_locally(ip: IPAddress) -> bool:
    """True when ``ip`` is assigned to an interface of this machine (or container):
    binding a socket to it succeeds. Any error but "address not available" counts as
    local, so a failing probe refuses rather than lets through. With Linux's
    ``ip_nonlocal_bind`` sysctl on, every bind succeeds and every LAN host is refused."""
    family = socket.AF_INET if ip.version == 4 else socket.AF_INET6
    try:
        sock = socket.socket(family, socket.SOCK_DGRAM)
    except OSError:
        return False  # no such address family here: it cannot be one of ours
    try:
        sock.bind((str(ip), 0))
    except OSError as exc:
        return exc.errno != errno.EADDRNOTAVAIL
    finally:
        sock.close()
    return True


def _in_container() -> bool:
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


def _default_routes(
    route: str = "/proc/net/route", route6: str = "/proc/net/ipv6_route"
) -> list[tuple[str, IPAddress]]:
    """The default routes as ``(interface, gateway)`` from ``/proc/net/route`` and
    ``/proc/net/ipv6_route`` (Linux; empty elsewhere or when unreadable)."""
    routes: list[tuple[str, IPAddress]] = []
    try:
        with open(route, encoding="ascii") as f:
            for line in f.readlines()[1:]:
                fields = line.split()
                # Iface Destination Gateway Flags ...: hex, little-endian; 0x2 = RTF_GATEWAY.
                if len(fields) > 3 and fields[1] == "00000000" and int(fields[3], 16) & 0x2:
                    gateway = ipaddress.IPv4Address(int(fields[2], 16).to_bytes(4, "little"))
                    routes.append((fields[0], gateway))
    except (OSError, ValueError):
        pass
    try:
        with open(route6, encoding="ascii") as f:
            for line in f:
                fields = line.split()
                # dest prefix src prefix next_hop metric refcnt use flags iface: hex.
                if len(fields) > 4 and fields[0] == "0" * 32 and fields[1] == "00":
                    hop = ipaddress.IPv6Address(int(fields[4], 16))
                    if hop != ipaddress.IPv6Address(0):
                        routes.append((fields[-1] if len(fields) > 9 else "", hop))
    except (OSError, ValueError):
        pass
    return routes


def _default_gateways(
    route: str = "/proc/net/route", route6: str = "/proc/net/ipv6_route"
) -> frozenset[IPAddress]:
    """The default routes' gateways (``_default_routes``)."""
    return frozenset(gateway for _, gateway in _default_routes(route, route6))


# The Home Assistant supervisor network (``hassio`` bridge, ``homeassistant`` alias): its
# gateway is the host, which exposes the HA core API and the radio proxy; the Supervisor
# (172.30.32.2), its DNS and observer and every other add-on live there too. The
# Supervisor numbers the IPv6 half of the bridge from fd0c:ac1e:2100::/48.
_HASSIO_NET = ipaddress.IPv4Network("172.30.32.0/23")
_HASSIO_NET_V6 = ipaddress.IPv6Network("fd0c:ac1e:2100::/48")
# Interfaces a container only sees when it shares the host's network namespace
# (``network_mode: host``): the engine's own bridges and the containers' veth ends.
_HOST_NETWORK_IFACE = re.compile(
    r"^(docker\d+|hassio|br-[0-9a-f]+|veth\w+|cni-podman\d+|podman\d+)$"
)


def _read_index(path: str) -> int:
    with open(path, encoding="ascii") as f:
        return int(f.read().strip())


def _own_link(iface: str, sys_net: str, depth: int = 0) -> bool:
    """True when ``iface`` is its own link (``iflink == ifindex`` in sysfs): a physical
    NIC, a bridge, Wi-Fi, a WireGuard tunnel. Also true for a VLAN or macvlan stacked
    on such a link in this namespace: the kernel lists its parent as a ``lower_<name>``
    entry only when the parent lives in the same namespace (MSHL-340). A bridged
    container's ``eth0`` is a veth (or a macvlan) whose link is its peer or parent in
    another namespace, with no ``lower_`` entry: False. Unreadable: False."""
    if not iface or "/" in iface or iface in (".", "..") or depth > 3:
        return False
    try:
        base = os.path.join(sys_net, iface)
        ifindex = _read_index(os.path.join(base, "ifindex"))
        iflink = _read_index(os.path.join(base, "iflink"))
    except (OSError, ValueError):
        return False
    if ifindex == iflink:
        return True
    try:
        lowers = [e[len("lower_") :] for e in os.listdir(base) if e.startswith("lower_")]
    except OSError:
        return False
    for lower in lowers:
        try:
            if _read_index(os.path.join(sys_net, lower, "ifindex")) != iflink:
                continue
        except (OSError, ValueError):
            continue
        if _own_link(lower, sys_net, depth + 1):
            return True
    return False


def _host_networking(
    dev: str = "/proc/net/dev",
    sys_net: str = "/sys/class/net",
    route: str = "/proc/net/route",
    route6: str = "/proc/net/ipv6_route",
) -> bool:
    """True when this container shares the host's network namespace. Two signals,
    both required: it lists the engine's bridges (``docker0``, ``hassio``, ``br-*``)
    or veth ends, which a bridged container never sees; and every default route
    leaves through an interface that is its own link, or a VLAN or macvlan on one
    (``_own_link``), never a veth
    whose peer is elsewhere. The second rules out a bridged container that runs its
    own engine (Docker-in-Docker shows ``docker0`` but routes through its veth
    ``eth0``, and its gateway is the real host). Unreadable, no default route or no
    match: False (the gateway stays refused)."""
    try:
        with open(dev, encoding="ascii", errors="replace") as f:
            names = [line.split(":", 1)[0].strip() for line in f.readlines()[2:] if ":" in line]
    except OSError:
        return False
    if not any(_HOST_NETWORK_IFACE.match(n) for n in names):
        return False
    ifaces = {iface for iface, _ in _default_routes(route, route6)}
    return bool(ifaces) and all(_own_link(iface, sys_net) for iface in ifaces)


def _gateway_is_host(ip: IPAddress) -> bool:
    """Inside a container, whether the default gateway ``ip`` is the engine's host. In
    bridge networking it always is. With host networking the gateway is the LAN router
    (a legitimate device), except the Home Assistant supervisor gateway, which stays
    refused; the host's own bridge addresses are caught by the bind probe."""
    if ip not in _default_gateways():
        return False
    if _host_networking():
        return ip.version == 4 and ip in _HASSIO_NET
    return True


def is_self_address(ip: IPAddress) -> bool:
    """True when ``ip`` reaches the machine Meshloom runs on (MSHL-330): an address of
    one of its interfaces (its LAN address, a Docker bridge such as 172.17.0.1, a
    Tailscale address...), or, inside a container, the default gateway, which is the
    Docker or Home Assistant host (not with ``network_mode: host``, where it is the LAN
    router: ``_gateway_is_host``). There the radio proxy (5051, no authentication)
    and the app's own port listen. The host's LAN address seen from inside a bridged
    container cannot be listed: that residual risk is in
    ``docs/dev/plugins-security.md``."""
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:a.b.c.d reaches the IPv4 interface a.b.c.d
    if _binds_locally(ip):
        return True
    return _in_container() and _gateway_is_host(ip)


def _is_ha_addon() -> bool:
    """The Home Assistant add-on: only ``MESHLOOM_INSTALL_KIND=addon``, which the add-on's
    ``run.py`` declares (``app/services/install_kind.py``), never inferred from the
    addresses in use: a deb, Docker or source install whose LAN happens to be numbered
    from 172.30.32.0/23 keeps reaching its devices there."""
    return detect_install_kind()[0] == "addon"


def is_supervisor_network_address(ip: IPAddress) -> bool:
    """True, in the Home Assistant add-on only, for an address of the supervisor network
    (``172.30.32.0/23``, ``fd0c:ac1e:2100::/48``; an IPv4-mapped spelling is unwrapped):
    the Supervisor, the other add-ons and the host's gateway are never a plugin's LAN
    device there (MSHL-350)."""
    ip = _unmapped(ip)
    if isinstance(ip, ipaddress.IPv4Address):
        inside = ip in _HASSIO_NET
    else:
        inside = ip in _HASSIO_NET_V6
    return inside and _is_ha_addon()


def check_lan_addresses(ips: Iterable[IPAddress]) -> None:
    """A declared LAN host must resolve to LAN addresses only: one other answer (public,
    loopback, link-local, another class) refuses it, so a name cannot be re-pointed
    (DNS rebinding) at the Internet; one address of the Meshloom host itself refuses it
    too (``egress_self_address``), whatever the name or literal, and so does, in the
    Home Assistant add-on, one address of the supervisor network
    (``egress_supervisor_address``, MSHL-350)."""
    for ip in ips:
        if is_metadata_address(ip) or is_documentation_address(ip):
            raise EgressBlocked("egress_private_address", always_refused=True)
        if not is_lan_address(ip):
            raise EgressBlocked("egress_lan_address")
        if is_self_address(ip):
            raise EgressBlocked("egress_self_address")
        if is_supervisor_network_address(ip):
            raise EgressBlocked("egress_supervisor_address")


def tls_check_may_be_off(ips: Iterable[IPAddress]) -> bool:
    """True when every resolved address is a private/LAN one: the only destinations whose
    certificate check a channel may turn off (a self-signed service on your network).
    A public address, or one host with a public answer among private ones, keeps it on."""
    resolved = list(ips)
    return bool(resolved) and all(is_private_address(ip) for ip in resolved)


def check_addresses(ips: Iterable[IPAddress], *, allow_private_network: bool) -> None:
    """Refuse when any address is refused: one private answer taints the host."""
    for ip in ips:
        if is_metadata_address(ip) or is_documentation_address(ip):
            raise EgressBlocked("egress_private_address", always_refused=True)
        if not allow_private_network and is_private_address(ip):
            raise EgressBlocked("egress_private_address")


def _normal_host(host: str) -> str:
    """One spelling per host: lowercase, no trailing dot, IP literals compressed."""
    host = host.lower().rstrip(".")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _split(url: str, schemes: frozenset[str]) -> tuple[str, str, int]:
    try:
        parsed = httpx.URL(url.strip())
        scheme = parsed.scheme.lower()
        # The IDNA2008 A-label httpx also sends in Host, so DNS, SNI and Host
        # name the same host (getaddrinfo would apply IDNA 2003: faß -> fass).
        host = _normal_host(parsed.raw_host.decode("ascii"))
        port = parsed.port
    except Exception:
        raise EgressBlocked("egress_scheme", permanent=True) from None
    if scheme not in schemes or not host:
        raise EgressBlocked("egress_scheme", permanent=True)
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
        if port is None:
            raise EgressBlocked("egress_scheme", permanent=True)
    if not 0 < port < 65536:
        raise EgressBlocked("egress_scheme", permanent=True)
    return scheme, host, port


_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True)
class HostAllowlist:
    """Exact hosts a caller may reach: ``host`` or ``host:port``, never a range.

    An entry is a host name (IDNA2008 A-label, like the URL host) or an IP
    literal (``[v6]`` with a port). An entry without a port allows only the
    default port of the URL's scheme. Wildcards, CIDR ranges, schemes, paths
    and user info are refused with ``ValueError`` when the list is built.

    The list does not restrict the scheme: a fixed-host caller (the Store
    client, the plugin ``http`` capability) must also pass
    ``schemes=frozenset({"https"})``, or ``http://github.com`` is allowed too.
    """

    entries: frozenset[tuple[str, int | None]] = field(default_factory=frozenset)

    @classmethod
    def of(cls, *entries: str) -> HostAllowlist:
        return cls.parse(entries)

    @classmethod
    def parse(cls, entries: Iterable[str]) -> HostAllowlist:
        parsed: set[tuple[str, int | None]] = set()
        for entry in entries:
            parsed.add(_parse_allowlist_entry(entry))
        return cls(frozenset(parsed))

    def allows(self, scheme: str, host: str, port: int) -> bool:
        if (host, port) in self.entries:
            return True
        return (host, None) in self.entries and _DEFAULT_PORTS.get(scheme) == port


def _parse_allowlist_entry(entry: str) -> tuple[str, int | None]:
    raw = entry.strip()
    if not raw or any(c in raw for c in "*/\\?#@ \t") or "://" in raw:
        raise ValueError("allowlist entry must be an exact host or host:port")
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        pass
    else:
        if ":" in raw:  # a bare IPv6 literal: no port
            return _normal_host(raw), None
    try:
        url = httpx.URL(f"https://{raw}/")
        host = _normal_host(url.raw_host.decode("ascii"))
        port = url.port
    except Exception:
        raise ValueError("allowlist entry must be an exact host or host:port") from None
    if not host or (port is not None and not 0 < port < 65536):
        raise ValueError("allowlist entry must be an exact host or host:port")
    return host, port


async def _resolve(host: str, port: int) -> tuple[IPAddress, ...]:
    try:
        return (ipaddress.ip_address(host),)
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise EgressBlocked("egress_unresolvable") from None
    ips: list[IPAddress] = []
    for family, _type, _proto, _canon, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        # Drop an IPv6 scope id ("fe80::1%eth0"): the class does not depend on it.
        ip = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise EgressBlocked("egress_unresolvable")
    return tuple(ips)


async def validate_destination(
    url: str,
    *,
    allow_private_network: bool = False,
    schemes: frozenset[str] = DEFAULT_SCHEMES,
    allowed_hosts: HostAllowlist | None = None,
    lan_hosts: HostAllowlist | None = None,
) -> ResolvedDestination:
    """Check the scheme, resolve A/AAAA and refuse a forbidden address class.

    With ``allowed_hosts``, a host outside the list is refused before the DNS
    lookup. ``lan_hosts`` are the declared devices of the user's network
    (plugin ``lan_hosts``, MSHL-282): a URL matching one is accepted over http
    or https whatever ``schemes`` says, skips ``allowed_hosts`` and
    ``allow_private_network``, and every address it resolves to must pass
    ``check_lan_addresses`` (``egress_lan_address`` otherwise). Raises
    ``EgressBlocked`` before any connection is made.
    """
    scheme, host, port = _split(url, schemes | DEFAULT_SCHEMES if lan_hosts else schemes)
    if lan_hosts is not None and lan_hosts.allows(scheme, host, port):
        if host in _METADATA_HOSTS:
            raise EgressBlocked("egress_private_address", permanent=True)
        ips = await _resolve(host, port)
        check_lan_addresses(ips)
        return ResolvedDestination(url=url.strip(), scheme=scheme, host=host, port=port, ips=ips)
    if allowed_hosts is not None and not allowed_hosts.allows(scheme, host, port):
        raise EgressBlocked("egress_host_not_allowed")
    if scheme not in schemes:  # only reached with lan_hosts: `_split` checked it otherwise
        raise EgressBlocked("egress_scheme", permanent=True)
    if host in _METADATA_HOSTS:
        raise EgressBlocked("egress_private_address", permanent=True)
    ips = await _resolve(host, port)
    try:
        check_addresses(ips, allow_private_network=allow_private_network)
    except EgressBlocked as exc:
        if exc.always_refused and _is_ip_literal(host):
            exc.permanent = True  # no resolver involved: the URL itself is refused
        raise
    return ResolvedDestination(url=url.strip(), scheme=scheme, host=host, port=port, ips=ips)


def pinned_url(dest: ResolvedDestination, ip: IPAddress) -> httpx.URL:
    """The destination URL with its host replaced by one validated IP."""
    return httpx.URL(dest.url).copy_with(host=str(ip))


class PinnedClient:
    """Sends to one of the validated IPs, with ``Host`` and SNI set to the hostname."""

    def __init__(self, client: httpx.AsyncClient, dest: ResolvedDestination) -> None:
        self._client = client
        self.destination = dest

    async def request(
        self,
        method: str,
        *,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        dest = self.destination
        sent_headers = self._headers(headers)
        last: httpx.ConnectError | None = None
        # Like a dual-stack connect: the next validated address only when the
        # connection itself failed, never once the request may have been sent.
        for ip in dest.ips:
            try:
                return await self._client.request(
                    method,
                    pinned_url(dest, ip),
                    content=content,
                    headers=sent_headers,
                    extensions={"sni_hostname": dest.host},
                )
            except httpx.ConnectError as exc:
                if is_tls_error(exc):
                    raise
                last = exc
        assert last is not None
        raise last

    async def post(
        self, *, content: bytes | None = None, headers: Mapping[str, str] | None = None
    ) -> httpx.Response:
        return await self.request("POST", content=content, headers=headers)

    @asynccontextmanager
    async def stream(
        self,
        method: str,
        *,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[httpx.Response]:
        """``request`` with the body left unread, for callers that cap it."""
        dest = self.destination
        sent_headers = self._headers(headers)
        last: httpx.ConnectError | None = None
        for ip in dest.ips:
            request = self._client.build_request(
                method,
                pinned_url(dest, ip),
                content=content,
                headers=sent_headers,
                extensions={"sni_hostname": dest.host},
            )
            try:
                response = await self._client.send(request, stream=True)
            except httpx.ConnectError as exc:
                if is_tls_error(exc):
                    raise
                last = exc
                continue
            try:
                yield response
            finally:
                await response.aclose()
            return
        assert last is not None
        raise last

    def _headers(self, headers: Mapping[str, str] | None) -> dict[str, str]:
        host_header = httpx.URL(self.destination.url).netloc.decode("ascii")
        return {**(headers or {}), "Host": host_header}


@asynccontextmanager
async def pinned_async_client(
    dest: ResolvedDestination,
    *,
    verify_tls: bool = True,
    timeout: httpx.Timeout | float,
) -> AsyncIterator[PinnedClient]:
    """An httpx client that never follows redirects and never re-resolves the host.

    ``trust_env`` is off: an HTTP proxy from the environment would resolve the
    host itself and void the pinning.
    """
    async with httpx.AsyncClient(
        follow_redirects=False, verify=verify_tls, timeout=timeout, trust_env=False
    ) as client:
        yield PinnedClient(client, dest)


# Never taken from a caller of ``follow_checked_redirects``: httpx sets the
# framing and Host itself, cookies are never sent, and hop-by-hop headers
# belong to the connection, not to the caller.
DROPPED_REQUEST_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "cookie",
        "connection",
        "keep-alive",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "upgrade",
    }
)
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# The only caller headers that follow a hop to another origin, unless the
# caller names more in ``cross_origin_headers``: anything else may be a
# credential (``Authorization``, ``X-Api-Key``, ``PRIVATE-TOKEN``...).
CROSS_ORIGIN_HEADERS = frozenset({"accept", "user-agent", "accept-encoding", "content-type"})
# Responses that carry no body whatever their headers say.
_BODYLESS_STATUSES = frozenset({204, 304})


def filter_request_headers(
    headers: Mapping[str, str] | None,
    header_filter: Callable[[str, str], bool] | None = None,
) -> dict[str, str]:
    """The caller's headers without ``DROPPED_REQUEST_HEADERS``.

    ``header_filter(name, value)`` may drop more (False drops); it can never
    bring a dropped header back.
    """
    kept: dict[str, str] = {}
    for name, value in (headers or {}).items():
        if name.lower() in DROPPED_REQUEST_HEADERS:
            continue
        if header_filter is not None and not header_filter(name, value):
            continue
        kept[name] = value
    return kept


@dataclass(frozen=True)
class CheckedResponse:
    """A response read under the caps. ``url`` is the last hop's URL, never an IP."""

    status_code: int
    headers: httpx.Headers
    content: bytes
    url: str
    redirects: int
    truncated: bool = False


async def follow_checked_redirects(
    method: str,
    url: str,
    *,
    timeout: httpx.Timeout | float,
    allowed_hosts: HostAllowlist | None = None,
    lan_hosts: HostAllowlist | None = None,
    allow_private_network: bool = False,
    schemes: frozenset[str] = DEFAULT_SCHEMES,
    max_redirects: int = 0,
    max_request_bytes: int | None = None,
    max_response_bytes: int | None = None,
    total_timeout: float | None = None,
    content: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    header_filter: Callable[[str, str], bool] | None = None,
    cross_origin_headers: Iterable[str] = (),
    verify_tls: bool = True,
    lan_verify_tls: bool = True,
    truncate_response: bool = False,
) -> CheckedResponse:
    """Send through the guard and follow redirects one validated hop at a time.

    Every hop, the first included, goes through ``validate_destination`` with
    the same allowlist, LAN hosts, schemes and private-network rule, then a pinned
    client. A body above ``max_request_bytes`` is refused before the first DNS
    lookup; a response is aborted as soon as it declares or streams more than
    ``max_response_bytes``. ``timeout`` applies per httpx operation,
    ``total_timeout`` (seconds) to the whole chain, so a server dripping bytes
    cannot hold the call open. With ``truncate_response`` a body past
    ``max_response_bytes`` is cut there instead (``truncated``), the rest
    left unread. With ``max_redirects=0`` a redirect is returned
    as is; one more redirect than allowed raises ``egress_too_many_redirects``.
    The request always asks for ``Accept-Encoding: identity`` (a caller's own
    value is replaced) and a compressed answer is refused
    (``egress_response_encoding``), so the cap counts the bytes on the wire.
    Cookies are never stored nor sent. A redirect to a URL with user info is refused
    (``egress_userinfo``). When a hop leaves the origin, only
    ``CROSS_ORIGIN_HEADERS`` and the names in ``cross_origin_headers`` follow
    it. ``lan_verify_tls=False`` skips the certificate check on the hops to a
    ``lan_hosts`` device only (a self-signed device, MSHL-331); every other hop
    keeps ``verify_tls``. Raises ``EgressBlocked``, ``httpx`` transport errors or
    ``TimeoutError``.
    """
    if max_request_bytes is not None and content is not None and len(content) > max_request_bytes:
        raise EgressBlocked("egress_request_too_large")
    sent_headers = {
        k: v
        for k, v in filter_request_headers(headers, header_filter).items()
        if k.lower() != "accept-encoding"
    }
    # Set after the filter, never the caller's: a compressed body could
    # inflate far past the cap before a decoded chunk is counted.
    sent_headers["Accept-Encoding"] = "identity"
    kept_cross_origin = CROSS_ORIGIN_HEADERS | {n.lower() for n in cross_origin_headers}
    async with asyncio.timeout(total_timeout):
        redirects = 0
        while True:
            dest = await validate_destination(
                url,
                allow_private_network=allow_private_network,
                schemes=schemes,
                allowed_hosts=allowed_hosts,
                lan_hosts=lan_hosts,
            )
            on_lan = lan_hosts is not None and lan_hosts.allows(dest.scheme, dest.host, dest.port)
            verify = verify_tls and (lan_verify_tls or not on_lan)
            # A fresh client per hop: no cookie jar carried from one host to the next.
            async with (
                pinned_async_client(dest, verify_tls=verify, timeout=timeout) as client,
                client.stream(method, content=content, headers=sent_headers) as response,
            ):
                location = response.headers.get("location")
                if response.status_code not in _REDIRECT_STATUSES or not location:
                    body, truncated = await _read_capped(
                        response, method, max_response_bytes, truncate=truncate_response
                    )
                    return CheckedResponse(
                        status_code=response.status_code,
                        headers=response.headers,
                        content=body,
                        url=dest.url,
                        redirects=redirects,
                        truncated=truncated,
                    )
                if max_redirects == 0:
                    return CheckedResponse(
                        status_code=response.status_code,
                        headers=response.headers,
                        content=b"",
                        url=dest.url,
                        redirects=0,
                    )
                status = response.status_code
            redirects += 1
            if redirects > max_redirects:
                raise EgressBlocked("egress_too_many_redirects")
            try:
                # Against the URL the caller asked for, never the pinned IP URL.
                next_url = httpx.URL(dest.url).join(location).copy_with(fragment=None)
            except Exception:
                raise EgressBlocked("egress_scheme") from None
            if next_url.userinfo:
                # A server never gets to make us send credentials (MSHL-366).
                raise EgressBlocked("egress_userinfo", permanent=True)
            if (status == 303 and method != "HEAD") or (status in (301, 302) and method == "POST"):
                method, content = "GET", None
                sent_headers = {
                    k: v for k, v in sent_headers.items() if k.lower() != "content-type"
                }
            previous = httpx.URL(dest.url)
            if (next_url.scheme, next_url.host, next_url.port) != (
                previous.scheme,
                previous.host,
                previous.port,
            ):
                sent_headers = {
                    k: v for k, v in sent_headers.items() if k.lower() in kept_cross_origin
                }
            url = str(next_url)


async def _read_capped(
    response: httpx.Response, method: str, limit: int | None, *, truncate: bool = False
) -> tuple[bytes, bool]:
    """The raw body, aborted (connection closed) once it exceeds ``limit`` bytes,
    and whether it was cut: with ``truncate`` the first ``limit`` bytes are kept
    instead of raising ``egress_response_too_large``.

    Never decoded: a ``Content-Encoding`` other than ``identity`` is refused.
    A HEAD, 204 or 304 answer has no body, so its ``Content-Length`` (the
    size of the resource) is not checked against the cap.
    """
    if method.upper() == "HEAD" or response.status_code in _BODYLESS_STATUSES:
        return b"", False
    encodings = response.headers.get("content-encoding", "").split(",")
    if any(e.strip().lower() not in ("", "identity") for e in encodings):
        raise EgressBlocked("egress_response_encoding")
    if limit is not None and not truncate:
        declared = response.headers.get("content-length")
        if declared is not None and declared.strip().isdigit() and int(declared) > limit:
            raise EgressBlocked("egress_response_too_large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_raw():
        if limit is not None and size + len(chunk) > limit:
            if not truncate:
                raise EgressBlocked("egress_response_too_large")
            chunks.append(chunk[: limit - size])
            return b"".join(chunks), True
        size += len(chunk)
        chunks.append(chunk)
    return b"".join(chunks), False


def is_tls_error(exc: BaseException) -> bool:
    """True when a transport error comes from the TLS handshake or certificate."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError | ssl.CertificateError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def error_code(exc: BaseException) -> str:
    """A stable code for a failed send, never the exception text.

    ``egress_*`` for a refusal, ``timeout``, ``tls_error``, ``service_error``
    (the destination answered an HTTP error) or ``connect_error``.
    """
    if isinstance(exc, EgressBlocked):
        return exc.code
    if isinstance(exc, httpx.HTTPStatusError):
        return "service_error"
    if isinstance(exc, httpx.TimeoutException | TimeoutError):
        return "timeout"
    if is_tls_error(exc):
        return "tls_error"
    return "connect_error"
