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
headers a caller must never set are dropped.

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
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

import httpx

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


# English fallbacks for API details; the UI translates errors.<code>.
EGRESS_MESSAGES: dict[str, str] = {
    "egress_scheme": "The address must be an http:// or https:// URL",
    "egress_private_address": "The address is on a private network (LAN, localhost); "
    "allow private networks for this destination to use it",
    "egress_unresolvable": "The host name was not found; try typing the IP address instead",
    "egress_host_not_allowed": "This host is not in the list of hosts allowed here",
    "egress_too_many_redirects": "The destination redirected too many times",
    "egress_request_too_large": "The request body is larger than allowed",
    "egress_response_too_large": "The answer is larger than allowed",
    "egress_response_encoding": "The answer is compressed, which is not allowed here",
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


def _normal_host(host: str) -> str:
    """One spelling per host: lowercase, no trailing dot, IP literals compressed."""
    host = host.lower().rstrip(".")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host


def _split(url: str, schemes: frozenset[str]) -> tuple[str, str, int]:
    try:
        parsed = httpx.URL(url.strip())
        scheme = parsed.scheme.lower()
        # The IDNA2008 A-label httpx also sends in Host, so DNS, SNI and Host
        # name the same host (getaddrinfo would apply IDNA 2003: faß -> fass).
        host = _normal_host(parsed.raw_host.decode("ascii"))
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
) -> ResolvedDestination:
    """Check the scheme, resolve A/AAAA and refuse a forbidden address class.

    With ``allowed_hosts``, a host outside the list is refused before the DNS
    lookup. Raises ``EgressBlocked`` before any connection is made.
    """
    scheme, host, port = _split(url, schemes)
    if allowed_hosts is not None and not allowed_hosts.allows(scheme, host, port):
        raise EgressBlocked("egress_host_not_allowed")
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
    truncate_response: bool = False,
) -> CheckedResponse:
    """Send through the guard and follow redirects one validated hop at a time.

    Every hop, the first included, goes through ``validate_destination`` with
    the same allowlist, schemes and private-network rule, then a pinned
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
    Cookies are never stored nor sent. When a hop leaves the origin, only
    ``CROSS_ORIGIN_HEADERS`` and the names in ``cross_origin_headers`` follow
    it. Raises ``EgressBlocked``, ``httpx`` transport errors or
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
            )
            # A fresh client per hop: no cookie jar carried from one host to the next.
            async with (
                pinned_async_client(dest, verify_tls=verify_tls, timeout=timeout) as client,
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
