"""Send quotas of the plugin instances and the global spacing of automatic sends.

- **Spacing.** At least `SEND_SPACING_SECONDS` (2 s) between two automatic sends,
  whatever sends them (a plugin instance or a legacy bot): repeaters need the
  time to go back to listening. `send_spacer` serialises the sends; a send that
  failed does not move the reference time.
- **Per-instance quotas.** Each instance of a plugin may send at most `per_hour`
  messages per clock hour and `per_day` per UTC day (fixed windows), counters
  stored in `plugin_send_quota` (`PluginSendQuotaRepository`) so a restart does not reset them. The defaults are
  `DEFAULT_PER_HOUR` (30) and `DEFAULT_PER_DAY` (200); the manifest may ask for
  more up to the caps of the permission registry (60 and 500), and the user may
  only lower them (`plugins.quotas`).

The per-Meshloom-instance and per-destination budgets and the loop guards are
Store T6b, on top of these. Rules: docs/dev/plugins.md, "Capabilities".
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.loop_lock import LoopBoundLock
from app.plugins.permissions import MESSAGES_SEND_MAX_PER_DAY, MESSAGES_SEND_MAX_PER_HOUR

SEND_SPACING_SECONDS = 2.0
DEFAULT_PER_HOUR = 30
DEFAULT_PER_DAY = 200

Period = Literal["hour", "day"]
PERIOD_SECONDS: dict[Period, int] = {"hour": 3600, "day": 86400}


# --- global spacing --------------------------------------------------------------


class SendSlot:
    """The right to send now; `sent()` once the send went out."""

    def __init__(self) -> None:
        self.ok = False

    def sent(self) -> None:
        self.ok = True


class SendSpacer:
    """Serialises automatic sends and keeps `spacing` seconds between them.

    `clock` and `sleep` are attributes so tests can drive them.
    """

    def __init__(
        self,
        spacing: float = SEND_SPACING_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.spacing = spacing
        self.clock = clock
        self.sleep = sleep
        self.last_send: float | None = None
        self._lock = LoopBoundLock()

    @contextlib.asynccontextmanager
    async def slot(self) -> AsyncIterator[SendSlot]:
        async with self._lock:
            if self.last_send is not None:
                wait = self.spacing - (self.clock() - self.last_send)
                if wait > 0:
                    await self.sleep(wait)
            slot = SendSlot()
            yield slot
            if slot.ok:
                self.last_send = self.clock()


send_spacer = SendSpacer()


# --- per-instance quotas ---------------------------------------------------------


@dataclass(frozen=True)
class SendLimits:
    per_hour: int
    per_day: int

    def of(self, period: Period) -> int:
        return self.per_hour if period == "hour" else self.per_day


def _bounded(value: Any, cap: int) -> int | None:
    return min(value, cap) if type(value) is int and value >= 1 else None


def effective_send_limits(
    permission: Mapping[str, Any], instance_quotas: Mapping[str, Any] | None
) -> SendLimits:
    """The limits of one instance: the manifest's request (or the default), capped,
    then lowered by the user's `quotas.messages.send` (never raised)."""
    user = (instance_quotas or {}).get("messages.send")
    user = user if isinstance(user, Mapping) else {}
    limits = []
    for key, default, cap in (
        ("per_hour", DEFAULT_PER_HOUR, MESSAGES_SEND_MAX_PER_HOUR),
        ("per_day", DEFAULT_PER_DAY, MESSAGES_SEND_MAX_PER_DAY),
    ):
        limit = _bounded(permission.get(key), cap) or default
        lowered = _bounded(user.get(key), cap)
        limits.append(min(limit, lowered) if lowered is not None else limit)
    return SendLimits(per_hour=limits[0], per_day=limits[1])


class QuotaExceeded(Exception):
    def __init__(self, period: Period, limit: int, retry_after: int):
        super().__init__(f"{limit} messages per {period}")
        self.period = period
        self.limit = limit
        self.retry_after = retry_after


class SendQuota:
    """Fixed-window send counters per instance (`PluginSendQuotaRepository`).

    `clock` (Unix seconds) is an attribute so tests control the windows. Callers
    hold `send_spacer` around `check()`, the send and `record()`, so the check and
    the increment cannot interleave with another send.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock

    def _window(self, period: Period) -> tuple[int, int]:
        """`(start, seconds left)` of the current window."""
        now = int(self.clock())
        length = PERIOD_SECONDS[period]
        start = now - now % length
        return start, start + length - now

    async def counts(self, instance_id: str) -> dict[Period, int]:
        from app.repository.plugins import PluginSendQuotaRepository

        stored = await PluginSendQuotaRepository.counts(instance_id)
        result: dict[Period, int] = {"hour": 0, "day": 0}
        for period in PERIOD_SECONDS:
            start, count = stored.get(period, (None, 0))
            if start == self._window(period)[0]:
                result[period] = count
        return result

    async def check(self, instance_id: str, limits: SendLimits) -> None:
        """Raise `QuotaExceeded` (the first full window) when one more send is over."""
        counts = await self.counts(instance_id)
        for period in PERIOD_SECONDS:
            limit = limits.of(period)
            if counts[period] >= limit:
                raise QuotaExceeded(period, limit, self._window(period)[1])

    async def record(self, instance_id: str) -> None:
        """Count one send in both windows (a stale window restarts at 1)."""
        from app.repository.plugins import PluginSendQuotaRepository

        await PluginSendQuotaRepository.increment(
            instance_id, {period: self._window(period)[0] for period in PERIOD_SECONDS}
        )

    async def forget(self, instance_id: str) -> None:
        from app.repository.plugins import PluginSendQuotaRepository

        await PluginSendQuotaRepository.forget(instance_id)


send_quota = SendQuota()
