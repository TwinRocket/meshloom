"""The built-in per-address ban (MSHL-422): persistent, in the database.

Fed by the same events as the ``AUTH_FAIL`` line (``app/auth/fail_log.py``
calls ``note_failure()`` with the fields of the line it just wrote), so the ban
and fail2ban judge the same failures with the same address:

- Only a line that would read ``proxied=0`` counts: an undeclared local
  reverse proxy is the address of every client behind it.
- Never a local address (``app/net/local_address.py``: loopback, RFC 1918,
  CGNAT, link-local, ULA): the LAN, the Docker gateway and a local proxy stay
  reachable whatever happens. Never under ingress (no line either).
- Nor, on Linux, a global IPv6 address inside a prefix that is on-link on one
  of the host's interfaces (``/proc/net/if_inet6``): a LAN client reaching
  Meshloom over the household's global /64. For the ban only: the
  ``AUTH_FAIL`` line keeps its own ``proxied`` rule. Unreadable (macOS,
  Windows) or empty (a Docker bridge network sees only the container's
  interfaces), it adds nothing and such a client counts as public.
- Through the relay (``via=relay``), the address is ``client_ip()``, the
  client the dispatcher read from the frame, never the tunnel: a dispatcher
  that sets no client, or a local one, bans nothing (the line then reads
  ``ip=-``). A relayed line is always ``proxied=0``: its forwarding headers
  come from the remote client, so adding one never escapes the ban.
- An address covered by the whitelist (``ip_ban_whitelist``) is never banned,
  and a ban on it stops applying: the whitelist always wins.

``ip_ban_max_failures`` failures within ``ip_ban_window_minutes`` (counted in
memory per key: IPv4 by address, IPv6 by its /64) ban the key for
``ip_ban_duration_minutes``: a row in ``ip_bans`` and an ``ip.banned`` audit
row. The failure count starts again after a restart; the bans do not
(``load()`` at startup).

``AuthMiddleware`` calls ``check()`` first, before any credential is read
(no argon2id work for a banned client): 429 ``auth_ip_banned`` with
``Retry-After``, or a refused WebSocket. A key with no ban in memory costs one
dict lookup. A key with one re-reads its row and the whitelist on every
request, so ``meshloom-admin unban`` or ``whitelist add`` run while the
service runs apply on the next request. Turned off (``ip_ban_enabled``),
nothing is counted and no ban applies (the rows stay, and apply again when it
is turned back on, until they end).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import IPv6Address, IPv6Network

from app.auth.models import AuthSettings
from app.net import local_address
from app.repository.ip_ban import (
    MAX_BANS,
    IPAddress,
    IpBan,
    IpBanRepository,
    ban_key,
    canonical_address,
)

logger = logging.getLogger("meshloom.auth")

BANNED_CODE = "auth_ip_banned"

# Keys whose failures are counted at once: past it, the oldest is forgotten.
MAX_TRACKED_KEYS = 4096

# On-link IPv6 prefixes of the host: re-read at most this often.
IF_INET6_PATH = "/proc/net/if_inet6"
HOST_PREFIXES_TTL_S = 60.0
# The shortest on-link prefix taken as the LAN (a wider one is not a LAN).
HOST_PREFIX_MIN_LENGTH = 48
# A whitelisted address is not re-read for this long (the whitelist wins
# without two database reads per request); a removal through the API applies
# at once (``whitelist_changed()``), one by ``meshloom-admin`` after it.
WHITELIST_CACHE_S = 10.0


@dataclass(frozen=True)
class _Config:
    enabled: bool = True
    max_failures: int = 10
    window_s: int = 600
    duration_s: int = 3600


_config = _Config()
_failures: dict[str, deque[float]] = {}
# key -> expires_at (epoch seconds) of the bans this process knows of.
_bans: dict[str, float] = {}
# Keys whose ban is being written (``_ban``).
_pending: dict[str, asyncio.Task[None]] = {}

# address -> until (``clock()``): whitelisted addresses found by ``check()``.
_whitelisted: dict[str, float] = {}
# (read at, networks): the host's on-link global IPv6 prefixes.
_host_prefixes: tuple[float, tuple[IPv6Network, ...]] | None = None

clock: Callable[[], float] = time.time


def read_host_prefixes(path: str = IF_INET6_PATH) -> tuple[IPv6Network, ...]:
    """The global IPv6 prefixes on-link on the host's interfaces (Linux).

    Each line of ``if_inet6`` is ``address ifindex prefix_len scope flags name``
    (hex); scope ``00`` is global. Unreadable or malformed: nothing."""
    networks: set[IPv6Network] = set()
    try:
        with open(path, encoding="ascii") as handle:
            lines = handle.read(65536).splitlines()
    except (OSError, ValueError):
        return ()
    for line in lines:
        fields = line.split()
        if len(fields) < 4 or fields[3] != "00":
            continue
        try:
            length = int(fields[2], 16)
            address = IPv6Address(int(fields[0], 16))
        except ValueError:
            continue
        if not HOST_PREFIX_MIN_LENGTH <= length <= 128 or not address.is_global:
            continue
        networks.add(IPv6Network((address, length), strict=False))
    return tuple(sorted(networks))


def _host_networks() -> tuple[IPv6Network, ...]:
    global _host_prefixes
    now = time.monotonic()
    if _host_prefixes is None or now - _host_prefixes[0] >= HOST_PREFIXES_TTL_S:
        _host_prefixes = (now, read_host_prefixes(IF_INET6_PATH))
    return _host_prefixes[1]


def _local(address: IPAddress) -> bool:
    if local_address.is_local(address):
        return True
    return address.version == 6 and any(address in network for network in _host_networks())


def is_local(address: IPAddress) -> bool:
    """A local address (``local_address.is_local`` or an on-link global IPv6
    prefix of the host): never banned, and the only peers "login-free on the
    local network" may apply to (``app/auth/providers/trusted_network.py``)."""
    return _local(address)


def bannable(raw: str | None) -> IPAddress | None:
    """The address a failure from ``raw`` counts against, or None.

    Surrounding whitespace counts nothing, as the ``AUTH_FAIL`` line shows
    ``ip=-`` for it (``fail_log.ip_field``)."""
    if raw is not None and raw != raw.strip():
        return None
    address = canonical_address(raw)
    if address is None or _local(address) or address.is_unspecified:
        return None
    return address


def apply(settings: AuthSettings) -> None:
    """Take the ``ip_ban_*`` settings (startup, and every settings write)."""
    global _config
    _config = _Config(
        enabled=settings.ip_ban_enabled,
        max_failures=settings.ip_ban_max_failures,
        window_s=settings.ip_ban_window_minutes * 60,
        duration_s=settings.ip_ban_duration_minutes * 60,
    )
    if not _config.enabled:
        _failures.clear()


async def load() -> None:
    """Read the settings and the running bans. Call at startup."""
    from app.repository.auth import AuthSettingsRepository

    apply(await AuthSettingsRepository.get())
    _bans.clear()
    for ban in await IpBanRepository.active(clock()):
        _bans[ban.address] = ban.expires_at


def reset() -> None:
    """Back to the defaults with nothing counted or banned (tests)."""
    global _config, clock, _host_prefixes
    _config = _Config()
    _failures.clear()
    _bans.clear()
    _whitelisted.clear()
    _host_prefixes = None
    for task in _pending.values():
        task.cancel()
    _pending.clear()
    clock = time.time


async def settle() -> None:
    """Wait for the bans being written (tests)."""
    while _pending:
        await asyncio.gather(*_pending.values(), return_exceptions=True)


def note_failure(ip: str | None, *, via: str, proxied: bool, route: str) -> None:
    """Count one failure (the fields of its ``AUTH_FAIL`` line). Sync; never raises."""
    try:
        if not _config.enabled or proxied or via == "ingress":
            return
        address = bannable(ip)
        if address is None:
            return
        key = ban_key(address)
        now = clock()
        if key in _pending or _bans.get(key, 0) > now:
            return
        window = _failures.pop(key, None)
        if window is None:
            window = deque()
            while len(_failures) >= MAX_TRACKED_KEYS:
                _failures.pop(next(iter(_failures)))
        _failures[key] = window  # most recent last
        window.append(now)
        cutoff = now - _config.window_s
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) < _config.max_failures:
            return
        failures = len(window)
        del _failures[key]
        from app.background_tasks import spawn

        _pending[key] = spawn(
            _ban(key, address, failures, route, now, _config.duration_s), name="ip-ban"
        )
    except Exception:
        logger.exception("Could not count an authentication failure for the ban")


async def _ban(
    key: str, address: IPAddress, failures: int, route: str, now: float, duration_s: int
) -> None:
    from app.auth import audit

    try:
        if await IpBanRepository.whitelisted(address):
            return
        ban = IpBan(
            address=key,
            banned_at=int(now),
            expires_at=int(now) + duration_s,
            failures=failures,
            route=route,
        )
        await IpBanRepository.ban(ban)
        _bans[key] = ban.expires_at
        _prune(now)
        logger.warning(
            "Banned %s for %d minutes after %d authentication failures",
            key,
            duration_s // 60,
            failures,
        )
        await audit.record(
            "ip.banned",
            "success",
            actor=audit.SYSTEM_ACTOR,
            target=f"ip:{key}",
            details={"failures": failures, "route": route, "minutes": duration_s // 60},
        )
    except Exception:
        logger.exception("Could not write the ban of %s", key)
    finally:
        _pending.pop(key, None)


def _prune(now: float) -> None:
    """Keep ``_bans`` like ``ip_bans``: no ended ban, at most ``MAX_BANS``."""
    for key in [key for key, expires_at in _bans.items() if expires_at <= now]:
        del _bans[key]
    if len(_bans) > MAX_BANS:
        for key in sorted(_bans, key=_bans.__getitem__)[: len(_bans) - MAX_BANS]:
            del _bans[key]


async def check(ip: str | None) -> int | None:
    """Seconds left on the ban of ``ip``, or None when it may proceed."""
    if not _bans or not _config.enabled:
        return None
    address = canonical_address(ip)
    if address is None:
        return None
    key = ban_key(address)
    expires_at = _bans.get(key)
    if expires_at is None:
        return None
    now = clock()
    if expires_at <= now:
        _bans.pop(key, None)
        return None
    if _local(address):
        return None  # a ban written before the host's prefix was known
    cached = str(address)
    if _whitelisted.get(cached, 0) > now:
        return None
    try:
        whitelisted = await IpBanRepository.whitelisted(address)
        ban = None if whitelisted else await IpBanRepository.get(key)
    except Exception:
        # Fail closed: the ban this process wrote still applies.
        logger.warning("Could not re-read the ban of %s", key, exc_info=True)
        return max(1, int(expires_at - now))
    if whitelisted:
        # The row stays; it applies again once the entry goes.
        _whitelisted.pop(cached, None)
        while len(_whitelisted) >= MAX_TRACKED_KEYS:
            _whitelisted.pop(next(iter(_whitelisted)))
        _whitelisted[cached] = now + WHITELIST_CACHE_S
        return None
    if ban is None or ban.expires_at <= now:
        _bans.pop(key, None)
        return None
    _bans[key] = ban.expires_at
    return max(1, int(ban.expires_at - now))


def forget(key: str | None = None) -> None:
    """Drop one ban (or all) from memory after an unban in this process."""
    if key is None:
        _bans.clear()
    else:
        _bans.pop(key, None)


def whitelist_changed() -> None:
    """Forget the whitelist hits: a removal in this process applies at once
    (from ``meshloom-admin``, within ``WHITELIST_CACHE_S``)."""
    _whitelisted.clear()
