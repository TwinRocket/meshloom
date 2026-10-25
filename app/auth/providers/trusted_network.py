"""Trusted networks: a listed CIDR signs in as a chosen user without a login (docs/dev/auth.md).

Home Assistant's ``trusted_networks``, closed whenever a proxy is in the loop.
The rules are ``auth_settings.trusted_networks`` (``[{cidr, user_id}]``,
``GET/PATCH /api/auth/settings``, admin), empty by default. The gate
(``app/auth/middleware.py``) asks ``resolve_trusted_user()`` only for a request
that sent no credential at all (no ``Authorization``, no session that resolved).
A rule applies only when **all** of these hold (``rule_blocker`` names the
first that does not; anything unexpected also refuses, fail closed):

- the request did not come through the relay or Home Assistant ingress
  (``via`` is ``lan``);
- the TCP peer (``peer_address``, before any trusted-proxy rewrite) is not a
  trusted proxy (``auth_settings.trusted_proxies``): a proxied request is never
  auto-signed in, whatever its forwarded address;
- the request carries none of the headers a proxy or tunnel adds
  (``PROXY_HEADERS``: ``X-Forwarded-For``, ``X-Forwarded-Proto``,
  ``Forwarded``, ``X-Real-IP``, ``CF-Connecting-IP`` and their relatives).
  cloudflared, Caddy, nginx, Traefik and ngrok all set at least one, so a proxy
  nobody declared still closes the rule;
- the peer is not loopback, unless the install is the Home Assistant add-on
  (``LOOPBACK_ALLOWED_KINDS``): on a package, a source checkout or a container
  that may share the host's network, a local proxy or tunnel (``ssh -L`` adds
  no header at all) makes every internet visitor arrive from ``127.0.0.1``;
- the ``Host`` header passes the strict guard (``host_allowed``, in
  ``app/middleware/host_guard.py``, whatever ``host_guard_mode`` says): an IP
  literal, ``localhost``, a ``.local``/``.home.arpa``/``.lan``/``.internal``
  name, this machine's short or full name, or a declared public URL. Without
  it a DNS-rebinding page would get a principal with no cookie at all;
- the mapped account exists, is enabled and has no pending password change.

The most specific matching rule wins (longest prefix, then the first listed).
The principal has ``via = "trusted"`` and the ambient credential
``trusted_network``: unsafe methods still need the Origin rule.

Every auto-login writes one ``login`` audit row (``details.method =
"trusted_network"``), at most once per client address, user and UTC day
(``_audited``, bounded), so a LAN tablet polling the API does not flood the log.

The rules and the declared public URLs the ``Host`` check needs are kept in
memory: read from the database on first use, replaced by ``apply()`` after
every ``AuthSettingsRepository.update()``. Every check before the account
lookup is synchronous and in memory, so a request from an unlisted or proxied
peer costs no database read.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.auth import audit
from app.auth.models import TrustedNetworkRule, User
from app.middleware import proxy_headers
from app.middleware.host_guard import host_allowed

if TYPE_CHECKING:
    from app.auth.models import AuthSettings

logger = logging.getLogger(__name__)

MAX_TRUSTED_NETWORKS = 32

# Headers a reverse proxy or a tunnel adds. Any of them closes every rule.
PROXY_HEADERS: tuple[str, ...] = (
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-host",
    "x-forwarded-port",
    "x-forwarded-server",
    "forwarded",
    "x-real-ip",
    "cf-connecting-ip",
    "cf-ray",
    "true-client-ip",
    "x-client-ip",
    "x-cluster-client-ip",
    "fastly-client-ip",
    "x-original-forwarded-for",
    "via",
)

# Install kinds where loopback is the container's own: the add-on has its own
# network namespace (``meshloom/config.yaml`` has no ``host_network``), and
# other add-ons reach it over the ``hassio`` network, never loopback.
LOOPBACK_ALLOWED_KINDS = frozenset({"addon"})

# Install kinds that run in a container, where the default route's gateway is
# the Docker bridge every published-port client may come from.
CONTAINER_KINDS = frozenset({"compose", "container", "addon"})

ROUTE_TABLE = Path("/proc/net/route")

_MAPPED = ipaddress.ip_network("::ffff:0:0/96")

# Shared address space (carrier-grade NAT, Tailscale): not ``is_private`` in
# Python, yet not reachable from the internet either.
_SHARED = ipaddress.ip_network("100.64.0.0/10")

# Bound of the "already audited today" set.
MAX_AUDITED = 4096

_rules: tuple[TrustedNetworkRule, ...] | None = None
_public_urls: tuple[str, ...] = ()
_audited: dict[tuple[str, int], int] = {}

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


# ---------------------------------------------------------------- rules


def normalize_cidr(raw: str) -> str:
    """A rule's network in canonical form (``192.168.1.7`` → ``192.168.1.7/32``).

    Raises ``ValueError`` for anything else, for a network wider than the
    trusted proxies allow (``proxy_headers.MIN_PREFIX``: a /0 rule would sign
    the whole internet in) and for an IPv4-mapped IPv6 entry (list the IPv4
    network, which also covers its mapped form).
    """
    network = ipaddress.ip_network(raw.strip(), strict=False)
    if network.prefixlen < proxy_headers.MIN_PREFIX[network.version]:
        raise ValueError("a trusted network is too wide")
    if network.version == 6 and network.subnet_of(_MAPPED):  # type: ignore[arg-type]
        raise ValueError("list the IPv4 network instead of its IPv4-mapped form")
    return str(network)


def _network(cidr: str) -> Network:
    return ipaddress.ip_network(cidr)


def _overlaps(a: str, b: str) -> bool:
    left, right = _network(a), _network(b)
    return left.version == right.version and left.overlaps(right)  # type: ignore[arg-type]


def overlapping_proxy(cidr: str, proxies: Iterable[str]) -> str | None:
    """The first trusted proxy ``cidr`` overlaps, or None."""
    return next((proxy for proxy in proxies if _overlaps(cidr, proxy)), None)


def public_or_wide(cidr: str) -> bool:
    """A network that is almost always a mistake: as wide as allowed (``/8``
    IPv4, ``/16`` IPv6) or not private (``is_private``; the shared address
    space ``100.64.0.0/10`` counts as private). Accepted, but named by the
    ``trusted_network_public_or_wide`` security notice."""
    network = _network(cidr)
    if network.prefixlen <= proxy_headers.MIN_PREFIX[network.version]:
        return True
    if network.version == 4 and network.subnet_of(_SHARED):  # type: ignore[arg-type]
        return False
    return not network.is_private


def covers_loopback(cidr: str) -> bool:
    network = _network(cidr)
    loopback = (
        ipaddress.ip_network("127.0.0.0/8")
        if network.version == 4
        else ipaddress.ip_network("::1/128")
    )
    return network.overlaps(loopback)  # type: ignore[arg-type]


def docker_gateways(install_kind: str | None = None) -> tuple[str, ...]:
    """The default gateway(s) of a container install (the Docker bridge), from
    ``/proc/net/route``. Empty outside a container or when unreadable."""
    kind = install_kind if install_kind is not None else _install_kind()
    if kind not in CONTAINER_KINDS:
        return ()
    try:
        lines = ROUTE_TABLE.read_text(encoding="ascii").splitlines()[1:]
    except (OSError, UnicodeError):
        return ()
    gateways: list[str] = []
    for line in lines:
        fields = line.split()
        if len(fields) < 3 or fields[1] != "00000000":
            continue
        try:
            raw = bytes.fromhex(fields[2])
        except ValueError:
            continue
        if len(raw) != 4 or raw == b"\x00\x00\x00\x00":
            continue
        gateway = str(ipaddress.IPv4Address(raw[::-1]))
        if gateway not in gateways:
            gateways.append(gateway)
    return tuple(gateways)


def covered_gateway(cidr: str, gateways: Iterable[str]) -> str | None:
    """The first gateway address ``cidr`` covers, or None."""
    network = _network(cidr)
    for gateway in gateways:
        address = ipaddress.ip_address(gateway)
        if address.version == network.version and address in network:
            return gateway
    return None


def loopback_allowed(install_kind: str | None = None) -> bool:
    kind = install_kind if install_kind is not None else _install_kind()
    return kind in LOOPBACK_ALLOWED_KINDS


def _install_kind() -> str:
    from app.services.install_kind import detect_install_kind

    return detect_install_kind()[0]


def valid_stored_rules(stored: Iterable[TrustedNetworkRule]) -> tuple[TrustedNetworkRule, ...]:
    """Keep the rules that pass the PATCH validation of their network, drop the rest.

    Only a count is logged: no value read from the settings row reaches a log line.
    """
    rules: list[TrustedNetworkRule] = []
    invalid = 0
    for rule in stored:
        try:
            cidr = normalize_cidr(rule.cidr)
        except ValueError:
            invalid += 1
            continue
        if len(rules) >= MAX_TRUSTED_NETWORKS:
            invalid += 1
            continue
        rules.append(TrustedNetworkRule(cidr=cidr, user_id=rule.user_id))
    if invalid:
        logger.warning("Ignoring %d invalid trusted network rule(s)", invalid)
    return tuple(rules)


def apply(auth_settings: AuthSettings) -> tuple[TrustedNetworkRule, ...]:
    """Replace the in-memory rules with the valid ``trusted_networks``, and the
    public URLs the ``Host`` check accepts with ``public_urls``."""
    global _rules, _public_urls
    _public_urls = tuple(auth_settings.public_urls)
    _rules = valid_stored_rules(auth_settings.trusted_networks)
    return _rules


async def current() -> tuple[TrustedNetworkRule, ...]:
    """The rules, read from the database on first use."""
    if _rules is None:
        from app.repository.auth import AuthSettingsRepository

        return apply(await AuthSettingsRepository.get())
    return _rules


def reset() -> None:
    """Forget the rules and the audit memory (tests, and a fresh process)."""
    global _rules, _public_urls
    _rules = None
    _public_urls = ()
    _audited.clear()


# ---------------------------------------------------------------- matching


def _parse_address(raw: str | None) -> Address | None:
    if not raw:
        return None
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def matching_rule(
    address: Address, rules: Iterable[TrustedNetworkRule]
) -> TrustedNetworkRule | None:
    """The most specific rule covering ``address`` (longest prefix, then the first listed)."""
    best: TrustedNetworkRule | None = None
    best_prefix = -1
    for rule in rules:
        network = _network(rule.cidr)
        if (
            network.version == address.version
            and address in network
            and network.prefixlen > best_prefix
        ):
            best, best_prefix = rule, network.prefixlen
    return best


def carries_proxy_headers(headers: Mapping[str, str]) -> bool:
    return any(headers.get(name) is not None for name in PROXY_HEADERS)


def rule_blocker(
    scope: Mapping[str, Any],
    headers: Mapping[str, str],
    via: str,
    *,
    trusted_proxies: Iterable[str],
    public_urls: Iterable[str] = (),
    install_kind: str | None = None,
) -> str | None:
    """Why no rule may apply to this request, or None. Checks the request only,
    never the rules (module docstring)."""
    if via != "lan":
        return "via"
    state = scope.get("state")
    if isinstance(state, Mapping) and state.get("via") is not None:
        # Anything that tagged the request (the relay) is not a direct LAN client.
        return "via"
    peer = _parse_address(proxy_headers.peer_address(scope))
    if peer is None:
        return "no_peer"
    if any(peer in _network(proxy) for proxy in trusted_proxies):
        return "trusted_proxy"
    if carries_proxy_headers(headers):
        return "proxy_headers"
    if peer.is_loopback and not loopback_allowed(install_kind):
        return "loopback"
    if not host_allowed(headers.get("host"), public_urls):
        return "host"
    return None


async def resolve_trusted_user(
    scope: Mapping[str, Any], headers: Mapping[str, str], via: str
) -> tuple[User, TrustedNetworkRule] | None:
    """The account a trusted network rule signs this request in as, or None.

    Reads the database only for the rules' first load and, once every request
    check passed, for the mapped account.
    """
    rules = await current()
    if not rules:
        return None
    peer = _parse_address(proxy_headers.peer_address(scope))
    if peer is None:
        return None
    rule = matching_rule(peer, rules)
    if rule is None:
        return None
    if rule_blocker(
        scope,
        headers,
        via,
        trusted_proxies=proxy_headers.current(),
        public_urls=_public_urls,
    ):
        return None
    from app.repository.auth import UserRepository

    user = await UserRepository.get(rule.user_id)
    if user is None or user.disabled or user.must_change_password:
        return None
    await _audit_login(str(peer), user, rule)
    return user, rule


async def _audit_login(ip: str, user: User, rule: TrustedNetworkRule) -> None:
    """One ``login`` row per client address, user and UTC day."""
    day = int(time.time()) // audit.SECONDS_PER_DAY
    key = (ip, user.id)
    if _audited.get(key) == day:
        return
    if len(_audited) >= MAX_AUDITED:
        stale = [k for k, d in _audited.items() if d != day]
        for k in stale:
            del _audited[k]
        if len(_audited) >= MAX_AUDITED:
            _audited.clear()
    # Marked before the write: concurrent first requests write one row.
    _audited[key] = day
    await audit.record(
        "login",
        "success",
        actor=audit.AuditActor(via="trusted", user_id=user.id, ip=ip),
        target=audit.user_target(user.id),
        details={"method": "trusted_network", "cidr": rule.cidr},
    )
