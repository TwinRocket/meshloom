"""The one egress guard for user-configured destinations (SSRF).

Every outbound request to a URL a user or a browser chose (system webhook, Web
Push endpoints, the notification test routes; later Apprise, Store plugins and
Links) goes through ``validate_destination`` before any connection, then
connects to one of the validated IPs (``pinned_async_client`` for httpx, the
pinned urllib3 connections in ``app/push/send.py`` for pywebpush), so the DNS
answer cannot change between the check and the send.

Address classes are tested range by range. ``ipaddress.is_private`` and
``is_global`` changed between Python 3.11 and 3.13 (IPv4-mapped IPv6,
100.64.0.0/10), so they are never used. See ``app/net/AGENTS.md``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import ssl
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
EgressCode = Literal["egress_scheme", "egress_private_address", "egress_unresolvable"]

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


# English fallbacks for API details; the UI translates errors.<code>.
EGRESS_MESSAGES: dict[str, str] = {
    "egress_scheme": "The address must be an http:// or https:// URL",
    "egress_private_address": "The address is on a private network (LAN, localhost); "
    "allow private networks for this destination to use it",
    "egress_unresolvable": "The host name was not found; try typing the IP address instead",
}


def egress_detail(code: str) -> dict[str, str]:
    """A coded HTTP error detail for a refusal: never the host, the IP or the URL."""
    return {"code": code, "message": EGRESS_MESSAGES.get(code, "Destination refused")}


class EgressBlocked(Exception):
    """A destination the guard refuses. ``code`` is stable and translated by the UI."""

    def __init__(self, code: EgressCode) -> None:
        super().__init__(code)
        self.code: EgressCode = code


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


def check_addresses(ips: Iterable[IPAddress], *, allow_private_network: bool) -> None:
    """Refuse when any address is refused: one private answer taints the host."""
    for ip in ips:
        if is_metadata_address(ip) or is_documentation_address(ip):
            raise EgressBlocked("egress_private_address")
        if not allow_private_network and is_private_address(ip):
            raise EgressBlocked("egress_private_address")


def _split(url: str, schemes: frozenset[str]) -> tuple[str, str, int]:
    try:
        parsed = httpx.URL(url.strip())
        scheme = parsed.scheme.lower()
        # The IDNA2008 A-label httpx also sends in Host, so DNS, SNI and Host
        # name the same host (getaddrinfo would apply IDNA 2003: faß -> fass).
        host = parsed.raw_host.decode("ascii").lower().rstrip(".")
        port = parsed.port
    except Exception:
        raise EgressBlocked("egress_scheme") from None
    if scheme not in schemes or not host:
        raise EgressBlocked("egress_scheme")
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
        if port is None:
            raise EgressBlocked("egress_scheme")
    if not 0 < port < 65536:
        raise EgressBlocked("egress_scheme")
    return scheme, host, port


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
) -> ResolvedDestination:
    """Check the scheme, resolve A/AAAA and refuse a forbidden address class.

    Raises ``EgressBlocked`` before any connection is made.
    """
    scheme, host, port = _split(url, schemes)
    if host in _METADATA_HOSTS:
        raise EgressBlocked("egress_private_address")
    ips = await _resolve(host, port)
    check_addresses(ips, allow_private_network=allow_private_network)
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
        host_header = httpx.URL(dest.url).netloc.decode("ascii")
        sent_headers = {**(headers or {}), "Host": host_header}
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
