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
  ``IP_WINDOW_S`` (an ``aiolimiter`` leaky bucket per address). It is off when
  the install kind is ``compose`` or ``container`` and no trusted proxy is set
  (``auth_settings.trusted_proxies``): behind Docker's userland proxy every
  client arrives from the gateway address, and one bad client would lock the
  whole household out.

The IP is ``client_ip()``: the TCP peer, or the client a trusted proxy
forwarded (``app/middleware/proxy_headers.py``). Usernames are compared by
``username_key()`` (``Admin`` and ``admin`` are one name).

**Bounded memory.** A pair or username entry is dropped ``FORGET_AFTER_S``
after its last failure once no delay runs; an IP bucket is dropped once it
has leaked empty. A sweep runs at most every ``SWEEP_INTERVAL_S``, and each
table holds at most ``MAX_KEYS`` entries (the least recently failed goes).

**Audit.** The route writes one ``login`` row per delay, not per refused
attempt: ``delayed`` for the pair and username rules, ``refused`` for the
per-IP rule (``LoginDelayed.first``), so a flood of refused attempts cannot
fill the audit table. Failures themselves are audited as before.

Not covered: ``Authorization: Basic`` keeps its own per-IP cooldown
(``app/security.py``), and installation codes are answered one at a time
(``app/routers/auth.py``). Concurrent attempts on one pair can each pass
``check()`` before the first failure is recorded; the argon2id queue (10)
bounds that burst.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal

from aiolimiter import AsyncLimiter

from app.repository.auth import InvalidUsernameError, username_key

PAIR_FREE_FAILURES = 5
PAIR_MAX_DELAY_S = 60.0
USERNAME_FREE_FAILURES = 5
USERNAME_INTERVAL_S = 1.0
IP_MAX_FAILURES = 100
IP_WINDOW_S = 900.0
FORGET_AFTER_S = 900.0
SWEEP_INTERVAL_S = 60.0
MAX_KEYS = 4096

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


def _detect_install_kind() -> str:
    from app.services.install_kind import detect_install_kind

    return detect_install_kind()[0]


def _trusted_proxies_set() -> bool:
    from app.middleware import proxy_headers

    return bool(proxy_headers.current())


class LoginLimiter:
    """The three rules of the module docstring. One instance per process: ``login_limiter``."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        install_kind: Callable[[], str] = _detect_install_kind,
        trusted_proxies_set: Callable[[], bool] = _trusted_proxies_set,
    ) -> None:
        self._clock = clock
        self._install_kind_fn = install_kind
        self._install_kind: str | None = None
        self._trusted_proxies_set = trusted_proxies_set
        self._pairs: dict[tuple[str, str], _Counter] = {}
        self._names: dict[str, _Counter] = {}
        self._ips: dict[str, _FailureWindow] = {}
        self._last_sweep = clock()

    def hard_ip_limit_active(self) -> bool:
        """Whether the per-IP refusal applies (off behind Docker without a trusted proxy)."""
        if self._install_kind is None:
            self._install_kind = self._install_kind_fn()
        return self._install_kind not in _SHARED_ADDRESS_KINDS or self._trusted_proxies_set()

    def check(self, ip: str | None, username: str) -> None:
        """Raise ``LoginDelayed`` unless this attempt may be checked now.

        Takes the username's slot when it is slowed down, so call it once per attempt.
        """
        now = self._clock()
        self._maybe_sweep(now)
        address = ip or ""
        name = _name_key(username)

        window = self._ips.get(address)
        if window is not None and self.hard_ip_limit_active() and not window.has_capacity():
            first, window.audited = not window.audited, True
            raise LoginDelayed("ip", window.retry_after(), first)

        pair = self._pairs.get((address, name))
        if pair is not None and pair.blocked_until > now:
            first, pair.audited = not pair.audited, True
            raise LoginDelayed("pair", pair.blocked_until - now, first)

        slowed = self._names.get(name)
        if slowed is not None and slowed.failures >= USERNAME_FREE_FAILURES:
            if slowed.blocked_until > now:
                first, slowed.audited = not slowed.audited, True
                raise LoginDelayed("username", slowed.blocked_until - now, first)
            slowed.blocked_until = now + USERNAME_INTERVAL_S

    async def record_failure(self, ip: str | None, username: str) -> None:
        """A wrong password (or unknown name) for this attempt."""
        now = self._clock()
        address = ip or ""
        name = _name_key(username)

        pair = self._touch(self._pairs, (address, name), now)
        pair.failures += 1
        pair.audited = False
        if pair.failures >= PAIR_FREE_FAILURES:
            delay = min(PAIR_MAX_DELAY_S, 2.0 ** (pair.failures - PAIR_FREE_FAILURES))
            pair.blocked_until = now + delay

        slowed = self._touch(self._names, name, now)
        slowed.failures += 1
        slowed.audited = False

        if not self.hard_ip_limit_active():
            return
        window = self._ips.pop(address, None)
        if window is None:
            self._make_room(self._ips)
            window = _FailureWindow(self._clock)
        self._ips[address] = window
        window.audited = False
        if window.has_capacity():
            await window.acquire()

    def record_success(self, ip: str | None, username: str) -> None:
        """The right password: forget the pair (the username and IP counts stay)."""
        self._pairs.pop((ip or "", _name_key(username)), None)

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
