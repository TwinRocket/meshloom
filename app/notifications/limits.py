"""The dispatcher's limits: token buckets, the daily quota, the global cap and the
persisted dedup keys (MSHL-125).

Everything here is in process memory (one Meshloom process owns the radio and the
database); only the dedup keys and the daily count survive a restart, through
``notification_state`` and ``notification_counters``. The buckets are
``aiolimiter.AsyncLimiter`` instances read with ``has_capacity()`` and charged with
``acquire()`` once capacity is known, so nothing here ever waits: a full bucket diverts
the job to the aggregation window of the dispatcher. The global cap is charged with
``ClockedLimiter.try_acquire()``, which tests and takes in one step.

``used_today`` counts admissions, while a restart reseeds it from ``notification_counters``
(actual sends): a job admitted but never sent counts before the restart and not after.
The quota is never exceeded, only slightly under-used.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from aiolimiter import AsyncLimiter

from app.notifications.channels import Channel, ChannelError, Limits, get_type

logger = logging.getLogger(__name__)

GLOBAL_PER_HOUR = 600
DAY_S = 86400
DEDUP_PREFIX = "dedup:"
DEDUP_MAX_ENTRIES = 20000
# Written keys waiting for the next counter flush are bounded like the live ones.
DEDUP_DIRTY_MAX = DEDUP_MAX_ENTRIES

# How long a dedup key is remembered, per event (prefix match): a contact once per key
# for a day, a radio outage once (its key carries the outage start), a message once per
# message id. ``telemetry.alert`` is absent on purpose: its hysteresis is the only dedup.
DEDUP_TTL_S: tuple[tuple[str, int], ...] = (
    ("contact.first_seen", DAY_S),
    ("radio.offline", DAY_S),
    ("message.", DAY_S),
)
# An urgent job passes a full bucket once per (channel, dedup key).
URGENT_PASS_TTL_S = DAY_S


class ClockedLimiter(AsyncLimiter):
    """``AsyncLimiter`` on the dispatcher's clock instead of the loop's, so the tests
    drive refills on a fake clock. Only ``has_capacity()`` and ``acquire()`` with room
    are used, so no waiter is ever parked."""

    def __init__(self, max_rate: float, time_period: float, clock: Callable[[], float]) -> None:
        super().__init__(max_rate, time_period)
        self._clock = clock
        self._last_check = clock()

    def try_acquire(self) -> bool:
        """Check and take one unit in a single synchronous step: no suspension point
        between the test and the charge, so nothing can drain the bucket in between."""
        if not self.has_capacity():
            return False
        self._level += 1
        return True

    def _leak(self) -> None:
        now = self._clock()
        if self._level:
            elapsed = now - self._last_check
            self._level = max(self._level - elapsed * self._rate_per_sec, 0)
        self._last_check = now


def effective_limits(channel: Channel) -> Limits:
    """The channel's own limits, else the defaults of its type (ntfy.sh gets a daily cap)."""
    try:
        defaults = get_type(channel.type, channel.preset).limits_for(channel.config)
    except ChannelError:
        defaults = Limits(None, None, None)
    return Limits(
        channel.rate_per_min if channel.rate_per_min is not None else defaults.per_min,
        channel.rate_per_hour if channel.rate_per_hour is not None else defaults.per_hour,
        channel.rate_per_day if channel.rate_per_day is not None else defaults.per_day,
    )


def next_utc_midnight(now: float) -> float:
    return (int(now) // DAY_S + 1) * DAY_S


@dataclass
class ChannelBuckets:
    """Minute and hour buckets of one channel, plus its daily quota (a count of the UTC
    day, started from what ``notification_counters`` already holds)."""

    limits: Limits
    clock: Callable[[], float]
    wall: Callable[[], float]
    minute: ClockedLimiter | None = None
    hour: ClockedLimiter | None = None
    day: int = -1
    used_today: int = 0
    day_loaded: bool = False

    def __post_init__(self) -> None:
        self.configure(self.limits)

    def configure(self, limits: Limits) -> None:
        """Rebuild the buckets that changed (the channel's limits were edited)."""
        if self.minute is None or limits.per_min != self.limits.per_min:
            self.minute = (
                ClockedLimiter(limits.per_min, 60.0, self.clock) if limits.per_min else None
            )
        if self.hour is None or limits.per_hour != self.limits.per_hour:
            self.hour = (
                ClockedLimiter(limits.per_hour, 3600.0, self.clock) if limits.per_hour else None
            )
        self.limits = limits

    def _roll(self) -> None:
        today = int(self.wall()) // DAY_S
        if today != self.day:
            self.day, self.used_today, self.day_loaded = today, 0, False

    @property
    def day_start_hour(self) -> int:
        return (int(self.wall()) // DAY_S) * 24

    def needs_day_load(self) -> bool:
        self._roll()
        return bool(self.limits.per_day) and not self.day_loaded

    def load_day(self, sent_today: int) -> None:
        self.used_today = max(self.used_today, sent_today)
        self.day_loaded = True

    def quota_exhausted(self) -> bool:
        self._roll()
        return bool(self.limits.per_day) and self.used_today >= (self.limits.per_day or 0)

    def has_capacity(self) -> bool:
        return all(b.has_capacity() for b in (self.minute, self.hour) if b is not None)

    async def take(self) -> None:
        """Charge one send to every bucket that has room (an urgent job past a full
        bucket takes what is left: it still counts, the bucket just cannot go higher).
        ``acquire()`` with room returns without suspending."""
        for bucket in (self.minute, self.hour):
            if bucket is not None and bucket.has_capacity():
                await bucket.acquire()
        self._roll()
        self.used_today += 1


class DedupStore:
    """Dedup keys with an expiry, in memory and written to ``notification_state`` (a
    batch every counter flush, once more at shutdown) so a restart forgets nothing."""

    def __init__(self, wall: Callable[[], float] = time.time) -> None:
        self._wall = wall
        self._seen: dict[str, int] = {}
        self._dirty: dict[str, int] = {}
        self._loaded = False

    async def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        from app.repository.notification_channels import NotificationJournalRepository

        try:
            rows = await NotificationJournalRepository.state_load_prefix(
                DEDUP_PREFIX, int(self._wall())
            )
        except Exception:
            logger.debug("notification dedup keys unreadable", exc_info=True)
            return
        for key, expires_at in rows.items():
            self._seen.setdefault(key, expires_at)
        self._evict(self._seen, DEDUP_MAX_ENTRIES)

    def seen(self, key: str) -> bool:
        expires_at = self._seen.get(key)
        if expires_at is None:
            return False
        if expires_at <= self._wall():
            del self._seen[key]
            return False
        return True

    def mark(self, key: str, ttl_s: int) -> None:
        expires_at = int(self._wall()) + ttl_s
        if len(self._seen) >= DEDUP_MAX_ENTRIES:
            self._prune()
            self._evict(self._seen, DEDUP_MAX_ENTRIES - DEDUP_MAX_ENTRIES // 10)
        self._seen[key] = expires_at
        if len(self._dirty) >= DEDUP_DIRTY_MAX:
            self._evict(self._dirty, DEDUP_DIRTY_MAX - DEDUP_DIRTY_MAX // 10)
        self._dirty[key] = expires_at

    @staticmethod
    def _evict(keys: dict[str, int], keep: int) -> None:
        """Drop the keys closest to expiry until at most ``keep`` remain."""
        excess = len(keys) - keep
        if excess > 0:
            for oldest in sorted(keys, key=lambda k: keys[k])[:excess]:
                del keys[oldest]

    def _prune(self) -> None:
        now = self._wall()
        for key in [k for k, at in self._seen.items() if at <= now]:
            del self._seen[key]

    async def flush(self) -> None:
        from app.repository.notification_channels import NotificationJournalRepository

        dirty, self._dirty = self._dirty, {}
        if not dirty:
            return
        try:
            await NotificationJournalRepository.state_set_many(
                {key: ("1", at) for key, at in dirty.items()}
            )
        except Exception:
            self._dirty = {**dirty, **self._dirty}
            self._evict(self._dirty, DEDUP_DIRTY_MAX)
            logger.debug("notification dedup keys not written", exc_info=True)


def dedup_ttl(event_name: str) -> int | None:
    for prefix, ttl in DEDUP_TTL_S:
        if event_name.startswith(prefix):
            return ttl
    return None
