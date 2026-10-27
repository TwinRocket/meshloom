"""Login delays (cadrage, "Tentatives et délais"): slow a password guesser down, lock nobody out.

``POST /api/auth/login`` asks ``login_limiter.check()`` before any password
work, so a refused attempt never reaches the argon2id semaphore
(``app/auth/providers/password.py``), and reports the outcome with
``record_failure()`` / ``record_success()``. Three rules, all in memory:

- **Per (IP, username) pair: progressive delay.** The first
  ``PAIR_FREE_FAILURES`` failures cost nothing. From then on each failure
  closes the pair for ``2 ** (failures - PAIR_FREE_FAILURES)`` seconds, capped
  at ``PAIR_MAX_DELAY_S``: an attempt inside that delay answers 429
  ``auth_rate_limited`` with ``retry_after``, without checking the password.
  The first attempt after the delay is checked normally, so the right password
  gets in; a success forgets the pair.
- **Per username: global slow-down.** Once a username has
  ``USERNAME_FREE_FAILURES`` failures, from any IP, it takes at most one
  attempt every ``USERNAME_INTERVAL_S`` (429 with ``retry_after`` of at most
  that interval). It never grows into a lockout: anyone can type ``admin``
  through the relay, and a hard lock per name would let them lock the owner
  out. Under a flood from many addresses the owner competes for that one slot
  per second; the per-IP rule and the edge lock of the relay are the answer
  to such a flood, not a lock on the name.
- **Per IP: hard refusal** beyond ``IP_MAX_FAILURES`` failures in
  ``IP_WINDOW_S`` (an ``aiolimiter`` leaky bucket per address).

The address is ``client_ip()``: the TCP peer, or the client a trusted proxy
forwarded (``app/middleware/proxy_headers.py``), keyed by ``address_key()``:
an IPv4 address as is, an IPv6 address by its /64 (one host usually holds a
whole /64 and could rotate inside it). Usernames are compared by
``username_key()`` (``Admin`` and ``admin`` are one name).

**Shared addresses.** The pair and per-IP rules apply only when the address
identifies a client. ``address_key()`` returns None, and only the username
rule applies, when the configuration says clients share one address: the
install kind is ``compose`` or ``container``, no trusted proxy is set
(``auth_settings.trusted_proxies``, read live) and the admin has not stated
that addresses identify clients (``auth_settings.addresses_identify_clients``,
Settings → Users & Access, read live), since behind Docker's userland proxy
every client may arrive from the gateway address. Docker's default port
publishing keeps the client's address, so the Access panel proposes that
setting when the admin's own request comes from a public address
(``client_is_public()``). Request headers never decide it: ``X-Forwarded-For`` (or ``X-Real-IP``, ``Forwarded``) from a peer
that is not a trusted proxy is ignored, the peer is the client, and the admin
gets a log line (once per peer) pointing to the Trusted proxies setting.

**Known devices.** A browser that signed in successfully carries a signed
known-device cookie (``app/auth/device_cookie.py``). An attempt for that
username with a valid one skips all three rules while its device has fewer
than ``DEVICE_MAX_FAILURES`` failures (the count is dropped ``FORGET_AFTER_S``
after the device's last failure, or on a success), so a guesser,
however fast and wherever it sits, cannot keep the owner's own browser out.
A device that fails that often loses the bypass and follows the ordinary
rules. An unknown browser on a shared address competes for the one slot a
second; on a per-address deployment it only meets that slot under a flood
from many addresses.

**Bounded memory.** A pair, username or device entry is dropped ``FORGET_AFTER_S``
after its last failure once no delay runs; an IP bucket is dropped once it
has leaked empty. A sweep runs at most every ``SWEEP_INTERVAL_S``, and each
table holds at most ``MAX_KEYS`` entries (the least recently failed goes).

**Audit.** The route writes one ``login`` row per delay, not per refused
attempt: ``delayed`` for the pair and username rules, ``refused`` for the
per-IP rule (``LoginDelayed.first``), so a flood of refused attempts cannot
fill the audit table. Failures themselves are audited as before.

**Device pairing.** ``POST /api/auth/pair`` (``app/auth/pairing.py``) counts a
bad code as a failure under one shared key with ``name_rule=False``: the pair
and per-IP rules apply to the client, the per-username slow-down does not
(one client must not slow every device down; a 128-bit code needs no global
rule). On a shared address no rule applies to it.

Not covered: installation codes are answered one at a time
(``app/routers/auth.py``); ``Authorization: Basic`` is never checked (MSHL-100). Concurrent attempts on one pair can each pass
``check()`` before the first failure is recorded; the argon2id queue (10)
bounds that burst.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal

from aiolimiter import AsyncLimiter

from app.repository.auth import InvalidUsernameError, username_key

logger = logging.getLogger(__name__)

PAIR_FREE_FAILURES = 5
PAIR_MAX_DELAY_S = 60.0
USERNAME_FREE_FAILURES = 5
USERNAME_INTERVAL_S = 1.0
IP_MAX_FAILURES = 100
IP_WINDOW_S = 900.0
FORGET_AFTER_S = 900.0
SWEEP_INTERVAL_S = 60.0
MAX_KEYS = 4096
# Failures after which a known device loses its bypass (until FORGET_AFTER_S
# without a failure, or a success through the ordinary rules).
DEVICE_MAX_FAILURES = 5
# Untrusted peers sending forwarding headers that were logged once.
_MAX_HINTS = 64

# Install kinds where every client may share the Docker gateway address.
_SHARED_ADDRESS_KINDS = frozenset({"compose", "container"})

DelayReason = Literal["pair", "username", "ip"]


class LoginDelayed(Exception):
    """Answer 429 ``auth_rate_limited``: retry in ``retry_after`` seconds.

    ``first`` is True for the first refusal since the key's last failure (audit it).
    """

    def __init__(self, reason: DelayReason, retry_after: float, first: bool) -> None:
        super().__init__(reason)
        self.reason: DelayReason = reason
        self.retry_after = retry_after
        self.first = first

    @property
    def retry_after_s(self) -> int:
        """Whole seconds for ``Retry-After``, at least 1."""
        return max(1, math.ceil(self.retry_after))


@dataclass
class _Counter:
    failures: int = 0
    last_failure: float = 0.0
    # Pair: end of the delay. Username: when the next attempt is allowed.
    blocked_until: float = 0.0
    # A refusal was audited since the last failure.
    audited: bool = False


class _FailureWindow(AsyncLimiter):
    """aiolimiter's leaky bucket, read on the limiter's clock (a fake one in tests).

    aiolimiter takes its time from the running loop (``self._loop.time()``);
    this hands it the limiter's clock instead. It is only ever acquired while
    ``has_capacity()`` holds, so it never waits and never needs a real loop.
    """

    __slots__ = ("_clock_loop", "audited")

    def __init__(self, clock: Callable[[], float]) -> None:
        super().__init__(IP_MAX_FAILURES, IP_WINDOW_S)
        self._clock_loop = SimpleNamespace(time=clock)
        self.audited = False

    @property
    def _loop(self):  # type: ignore[override]
        return self._clock_loop

    def is_empty(self) -> bool:
        return self.has_capacity(self.max_rate)

    def retry_after(self) -> float:
        """Seconds until one more failure fits."""
        self.has_capacity()  # leaks up to now
        excess = self._level + 1 - self.max_rate
        return max(0.0, excess * self.time_period / self.max_rate)


def _name_key(username: str) -> str:
    try:
        return username_key(username)
    except InvalidUsernameError:
        return username.casefold()


# Headers a reverse proxy sets (cadrage, "Réseaux de confiance"). Only their
# presence is read here; app/middleware/proxy_headers.py interprets them.
_FORWARDING_HEADERS = frozenset({b"x-forwarded-for", b"x-real-ip", b"forwarded"})


def _carries_forwarding_headers(scope: Mapping[str, Any]) -> bool:
    return any(name.lower() in _FORWARDING_HEADERS for name, _ in scope.get("headers") or ())


def _parse_address(raw: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if not raw:
        return None
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _is_trusted_proxy(peer: str | None, proxies: tuple[str, ...]) -> bool:
    address = _parse_address(peer)
    return address is not None and any(address in ipaddress.ip_network(proxy) for proxy in proxies)


def forwarded_by_untrusted_peer(scope: Mapping[str, Any]) -> bool:
    """Forwarding headers from a peer that is not a trusted proxy: the headers are
    ignored, and the client address is that proxy, shared by everyone behind it."""
    from app.middleware import proxy_headers

    return _carries_forwarding_headers(scope) and not _is_trusted_proxy(
        proxy_headers.peer_address(scope), proxy_headers.current()
    )


def client_address_key(raw: str) -> str:
    """IPv4 by address, IPv6 by its /64: one host gets a whole /64 to rotate in."""
    address = _parse_address(raw)
    if address is None:
        return raw
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def _detect_install_kind() -> str:
    from app.services.install_kind import detect_install_kind

    return detect_install_kind()[0]


def _trusted_proxies_set() -> bool:
    from app.middleware import proxy_headers

    return bool(proxy_headers.current())


def _addresses_identify_clients() -> bool:
    from app.middleware import proxy_headers

    return proxy_headers.addresses_identify_clients()


def shared_address_install() -> bool:
    """Whether this install kind may see every client as the Docker gateway."""
    return _detect_install_kind() in _SHARED_ADDRESS_KINDS


def client_is_public(scope: Mapping[str, Any]) -> bool:
    """Whether the request's client address (after a trusted proxy's rewrite) is a
    global one. An address that does not parse counts as public (fail closed)."""
    client = scope.get("client")
    address = _parse_address(client[0] if client else None)
    return address is None or address.is_global


class LoginLimiter:
    """The three rules of the module docstring. One instance per process: ``login_limiter``."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        install_kind: Callable[[], str] = _detect_install_kind,
        trusted_proxies_set: Callable[[], bool] = _trusted_proxies_set,
        addresses_identify_clients: Callable[[], bool] = _addresses_identify_clients,
    ) -> None:
        self._clock = clock
        self._install_kind_fn = install_kind
        self._install_kind: str | None = None
        self._trusted_proxies_set = trusted_proxies_set
        self._addresses_identify_clients = addresses_identify_clients
        self._pairs: dict[tuple[str, str], _Counter] = {}
        self._names: dict[str, _Counter] = {}
        self._ips: dict[str, _FailureWindow] = {}
        self._devices: dict[str, _Counter] = {}
        self._hinted_peers: set[str] = set()
        self._last_sweep = clock()

    def address_rules_active(self) -> bool:
        """False behind Docker without a trusted proxy, unless the admin stated that
        addresses identify clients: every client may share the gateway address."""
        if self._install_kind is None:
            self._install_kind = self._install_kind_fn()
        return (
            self._install_kind not in _SHARED_ADDRESS_KINDS
            or self._trusted_proxies_set()
            or self._addresses_identify_clients()
        )

    def address_key(self, scope: Mapping[str, Any]) -> str | None:
        """The client's address key for the pair and per-IP rules, or None when the
        address does not identify a client (module docstring, "Shared addresses")."""
        if not self.address_rules_active():
            return None
        from app.middleware import proxy_headers

        client = scope.get("client")
        if not client or not client[0]:
            return None
        if forwarded_by_untrusted_peer(scope):
            # The headers are ignored (the peer is the client): only the admin is told.
            self._hint_untrusted_proxy(proxy_headers.peer_address(scope))
        return client_address_key(client[0])

    def _hint_untrusted_proxy(self, peer: str | None) -> None:
        if peer is None or peer in self._hinted_peers or len(self._hinted_peers) >= _MAX_HINTS:
            return
        self._hinted_peers.add(peer)
        logger.warning(
            "Sign-in attempts from %s carry forwarding headers, but it is not a trusted "
            "proxy: every client behind it counts as %s for the login delays. If it is "
            "your reverse proxy, list it in Settings > Local Configuration > Trusted proxies.",
            peer,
            peer,
        )

    def known_device_trusted(self, device: str | None) -> bool:
        """Whether a known device still has its bypass (fewer than
        ``DEVICE_MAX_FAILURES`` recent failures)."""
        if device is None:
            return False
        entry = self._devices.get(device)
        return entry is None or entry.failures < DEVICE_MAX_FAILURES

    def check(
        self,
        address: str | None,
        username: str,
        device: str | None = None,
        *,
        name_rule: bool = True,
    ) -> None:
        """Raise ``LoginDelayed`` unless this attempt may be checked now.

        ``address`` comes from ``address_key()``: None applies the username rule only.
        ``device`` is a known-device id (``app/auth/device_cookie.py``): while it
        has its budget, no rule applies to it. Takes the username's slot when it
        is slowed down, so call it once per attempt. ``name_rule=False`` (the
        device pairing redemption, whose "username" is one shared key) applies
        the per-address rules only, so one client cannot slow everyone down.
        """
        now = self._clock()
        self._maybe_sweep(now)
        name = _name_key(username)
        if self.known_device_trusted(device):
            return

        if address is not None:
            window = self._ips.get(address)
            if window is not None and not window.has_capacity():
                first, window.audited = not window.audited, True
                raise LoginDelayed("ip", window.retry_after(), first)

            pair = self._pairs.get((address, name))
            if pair is not None and pair.blocked_until > now:
                first, pair.audited = not pair.audited, True
                raise LoginDelayed("pair", pair.blocked_until - now, first)

        if not name_rule:
            return
        slowed = self._names.get(name)
        if slowed is not None and slowed.failures >= USERNAME_FREE_FAILURES:
            if slowed.blocked_until > now:
                first, slowed.audited = not slowed.audited, True
                raise LoginDelayed("username", slowed.blocked_until - now, first)
            slowed.blocked_until = now + USERNAME_INTERVAL_S

    async def record_failure(
        self,
        address: str | None,
        username: str,
        device: str | None = None,
        *,
        name_rule: bool = True,
    ) -> None:
        """A wrong password (or unknown name) for this attempt. ``name_rule``
        as for ``check()``: False leaves the username count alone."""
        now = self._clock()
        name = _name_key(username)

        if device is not None:
            spent = self._touch(self._devices, device, now)
            spent.failures += 1

        if name_rule:
            slowed = self._touch(self._names, name, now)
            slowed.failures += 1
            slowed.audited = False

        if address is None:
            return
        pair = self._touch(self._pairs, (address, name), now)
        pair.failures += 1
        pair.audited = False
        if pair.failures >= PAIR_FREE_FAILURES:
            delay = min(PAIR_MAX_DELAY_S, 2.0 ** (pair.failures - PAIR_FREE_FAILURES))
            pair.blocked_until = now + delay

        window = self._ips.pop(address, None)
        if window is None:
            self._make_room(self._ips)
            window = _FailureWindow(self._clock)
        self._ips[address] = window
        window.audited = False
        if window.has_capacity():
            await window.acquire()

    def record_success(self, address: str | None, username: str, device: str | None = None) -> None:
        """The right password: forget the pair and the device's failures (the
        username and IP counts stay)."""
        if address is not None:
            self._pairs.pop((address, _name_key(username)), None)
        if device is not None:
            self._devices.pop(device, None)

    def _touch(self, table: dict[Any, _Counter], key: Any, now: float) -> _Counter:
        """The entry for ``key``, moved to the end (most recent failure last)."""
        entry = table.pop(key, None)
        if entry is None:
            self._make_room(table)
            entry = _Counter()
        entry.last_failure = now
        table[key] = entry
        return entry

    @staticmethod
    def _make_room(table: dict) -> None:
        while len(table) >= MAX_KEYS:
            table.pop(next(iter(table)))

    def _maybe_sweep(self, now: float) -> None:
        if now - self._last_sweep < SWEEP_INTERVAL_S:
            return
        self._last_sweep = now
        self.sweep(now)

    def sweep(self, now: float | None = None) -> None:
        """Drop every entry whose window is empty."""
        now = self._clock() if now is None else now
        self._drop_forgotten(self._pairs, now)
        self._drop_forgotten(self._names, now)
        self._drop_forgotten(self._devices, now)
        for address in [address for address, window in self._ips.items() if window.is_empty()]:
            del self._ips[address]

    @staticmethod
    def _drop_forgotten(table: dict[Any, _Counter], now: float) -> None:
        for key in [
            key
            for key, entry in table.items()
            if entry.blocked_until <= now and now - entry.last_failure >= FORGET_AFTER_S
        ]:
            del table[key]

    def sizes(self) -> tuple[int, int, int]:
        """Entries held: pairs, usernames, IPs (tests, diagnostics)."""
        return len(self._pairs), len(self._names), len(self._ips)


login_limiter = LoginLimiter()


def reset_login_limiter() -> None:
    """A fresh limiter (tests)."""
    global login_limiter
    login_limiter = LoginLimiter()
