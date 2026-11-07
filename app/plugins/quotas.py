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

- **Send guards (Store T6b).** Host-side, on top of the per-instance quotas, for
  every plugin instance together: the Meshloom-wide budget (`instance`, default 120
  sends per clock hour, cap 300), the per-destination quotas (`destination`: 20 per
  hour per channel, cap 60; 10 per hour per contact, cap 30), the sender cooldown
  (`sender_cooldown`: one reply per 30 s to the same sender per plugin instance) and
  the quiet window (`quiet_window`: no reply to a message received less than 5 s
  after a send of the same plugin instance to the same destination). They cut the
  loops between auto-responders (two instances answering each other included), which
  a per-plugin quota cannot. Counters and stamps live in `plugin_send_guard`, the
  user's lowered limits in `plugin_send_limits` (`PluginSendGuardRepository`), so a
  restart resets nothing. A refusal is `quota_exceeded` with `params.scope`.

Rules: docs/dev/plugins.md, "Capabilities".
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.loop_lock import LoopBoundLock
from app.plugins.permissions import MESSAGES_SEND_MAX_PER_DAY, MESSAGES_SEND_MAX_PER_HOUR

logger = logging.getLogger(__name__)

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


# --- send guards: Meshloom-wide budget, destinations, loop cuts -------------------

SENDER_COOLDOWN_SECONDS = 30
QUIET_WINDOW_SECONDS = 5
# A send tied to no event is a reply to the last message of its destination only while
# that message is this recent; an older one (a timer, a daily reminder) is unsolicited.
UNTIED_REPLY_HORIZON_SECONDS = 30

GuardScope = Literal["instance", "destination", "sender_cooldown", "quiet_window"]
GuardLimitName = Literal["instance_per_hour", "channel_per_hour", "contact_per_hour"]

# name -> (default, cap). The user may lower a limit, never raise it past its default
# (the caps are the ceiling no setting or future raise may cross).
GUARD_LIMITS: dict[GuardLimitName, tuple[int, int]] = {
    "instance_per_hour": (120, 300),
    "channel_per_hour": (20, 60),
    "contact_per_hour": (10, 30),
}

_HOUR = PERIOD_SECONDS["hour"]
_HIT_LOG_INTERVAL = 60.0


@dataclass(frozen=True)
class GuardLimits:
    instance_per_hour: int
    channel_per_hour: int
    contact_per_hour: int


def effective_guard_limits(stored: Mapping[str, Any] | None) -> GuardLimits:
    """The defaults, lowered by the stored values (a stored value above its default,
    or not a positive integer, is ignored: limits only go down)."""
    values: dict[str, int] = {}
    for name, (default, cap) in GUARD_LIMITS.items():
        value = (stored or {}).get(name)
        values[name] = value if type(value) is int and 1 <= value <= min(default, cap) else default
    return GuardLimits(**values)


class GuardRefused(Exception):
    """A send guard fired. `params` is what the child gets in `params`."""

    def __init__(self, scope: GuardScope, message: str, **params: Any):
        super().__init__(message)
        self.scope: GuardScope = scope
        self.params: dict[str, Any] = {"scope": scope, **params}


def destination_subject(*, is_dm: bool, key: str) -> str:
    return ("contact:" + key.lower()) if is_dm else ("channel:" + key.upper())


class SendGuard:
    """The host-side send guards. `clock` (Unix seconds) is an attribute so tests
    control the windows and stamps. Callers hold `send_spacer` around `check()`, the
    send and `record()`, like `SendQuota`, so a check and its count cannot interleave
    with another send."""

    def __init__(self, clock: Callable[[], float] = time.time, store: Any = None) -> None:
        self.clock = clock
        # `PluginSendGuardRepository` (imported on first use), or a stand-in with the
        # same four methods: the two-host simulation gives each host its own.
        self._store = store
        self._hits: dict[str, dict[str, Any]] = {}
        # (instance, destination subject) -> (sender, received_at) of the last message
        # the instance was handed from that destination (memory only).
        self._inbound: dict[tuple[str, str], tuple[str | None, int]] = {}
        self._last_logged: dict[tuple[str, str], float] = {}

    @property
    def store(self) -> Any:
        if self._store is None:
            from app.repository.plugins import PluginSendGuardRepository

            self._store = PluginSendGuardRepository
        return self._store

    # Limits.

    async def limits(self) -> GuardLimits:
        return effective_guard_limits(await self.store.limits())

    async def set_limits(self, **values: int | None) -> GuardLimits:
        """Lower limits (`None` restores the default). A value below 1 or above the
        default is refused (`ValueError`), an unknown name too; nothing is stored then."""
        for name, value in values.items():
            if name not in GUARD_LIMITS:
                raise ValueError(f"unknown send limit {name!r}")
            if value is None:
                continue
            default, cap = GUARD_LIMITS[name]  # type: ignore[index]
            if type(value) is not int or not 1 <= value <= min(default, cap):
                raise ValueError(f"{name} must be an integer from 1 to {min(default, cap)}")
        await self.store.set_limits(values)
        return await self.limits()

    # Check and count.

    def _window(self) -> tuple[int, int]:
        """`(start, seconds left)` of the current clock hour."""
        now = int(self.clock())
        start = now - now % _HOUR
        return start, start + _HOUR - now

    async def _hour_count(self, scope: str, subject: str, window_start: int) -> int:
        row = await self.store.get(scope, subject)
        return row[1] if row is not None and row[0] == window_start else 0

    async def check(
        self,
        instance_id: str,
        *,
        is_dm: bool,
        key: str,
        sender: str | None,
        received_at: int | None,
    ) -> None:
        """Raise `GuardRefused` when one more send of `instance_id` to the destination is
        over a guard. `sender` is who the send replies to (None: not a reply, so no
        cooldown nor quiet window); `received_at` when that message arrived (Unix
        seconds, None = now)."""
        now = int(self.clock())
        if sender is not None:
            stamp = await self.store.get("cooldown", instance_id + "|" + sender)
            if stamp is not None and 0 <= now - stamp[0] < SENDER_COOLDOWN_SECONDS:
                raise GuardRefused(
                    "sender_cooldown",
                    f"one reply per {SENDER_COOLDOWN_SECONDS} s to the same sender",
                    limit=SENDER_COOLDOWN_SECONDS,
                    retry_after=SENDER_COOLDOWN_SECONDS - (now - stamp[0]),
                )
            destination_stamp = await self.store.get(
                "quiet", instance_id + "|" + destination_subject(is_dm=is_dm, key=key)
            )
            if destination_stamp is not None:
                arrived = received_at if received_at is not None else now
                if 0 <= arrived - destination_stamp[0] < QUIET_WINDOW_SECONDS:
                    raise GuardRefused(
                        "quiet_window",
                        f"no reply to a message received within {QUIET_WINDOW_SECONDS} s of a send",
                        limit=QUIET_WINDOW_SECONDS,
                    )
        start, left = self._window()
        limits = await self.limits()
        subject = destination_subject(is_dm=is_dm, key=key)
        per_destination = limits.contact_per_hour if is_dm else limits.channel_per_hour
        if await self._hour_count("destination", subject, start) >= per_destination:
            raise GuardRefused(
                "destination",
                f"at most {per_destination} messages per hour to this "
                + ("contact" if is_dm else "channel"),
                period="hour",
                limit=per_destination,
                retry_after=left,
                kind="contact" if is_dm else "channel",
            )
        if await self._hour_count("instance", "", start) >= limits.instance_per_hour:
            raise GuardRefused(
                "instance",
                f"at most {limits.instance_per_hour} messages per hour from all plugins",
                period="hour",
                limit=limits.instance_per_hour,
                retry_after=left,
            )

    async def record(self, instance_id: str, *, is_dm: bool, key: str, sender: str | None) -> None:
        """Count one send that went out in both hourly windows and stamp the quiet
        window of `(instance, destination)` and the cooldown of `(instance, sender)`."""
        now = int(self.clock())
        await self.store.record_send(
            window_start=self._window()[0],
            destination=destination_subject(is_dm=is_dm, key=key),
            instance_id=instance_id,
            now=now,
            sender=sender,
            keep_cooldown_after=now - SENDER_COOLDOWN_SECONDS,
            keep_quiet_after=now - QUIET_WINDOW_SECONDS,
        )

    # The last inbound message per (instance, destination): the loop guards must not
    # depend on a plugin tying its send to an event, so a send that cannot be tied is
    # treated as a reply to the most recent message of its destination.

    @staticmethod
    def sender_of(params: Mapping[str, Any]) -> str | None:
        """Who sent a message event: `key:<sender_key>` (direct message) or
        `name:<sender_name>` (a channel message has no key)."""
        if params.get("sender_key"):
            return "key:" + str(params["sender_key"]).lower()
        if params.get("sender_name"):
            return "name:" + str(params["sender_name"]).lower()
        return None

    def note_inbound(self, instance_id: str, params: Mapping[str, Any]) -> None:
        """Remember a message handed to `instance_id` (outgoing ones are not replied to)."""
        if params.get("is_outgoing"):
            return
        key = params.get("channel_key") if not params.get("is_dm") else params.get("sender_key")
        key = key or params.get("conversation_key")
        if not key:
            return
        received = params.get("received_at")
        self._inbound[
            (instance_id, destination_subject(is_dm=bool(params.get("is_dm")), key=str(key)))
        ] = (
            self.sender_of(params),
            received if type(received) is int else int(self.clock()),
        )

    def last_inbound(
        self, instance_id: str, *, is_dm: bool, key: str
    ) -> tuple[str | None, int] | None:
        """The last message handed to the instance from this destination, None when there
        is none or it is `UNTIED_REPLY_HORIZON_SECONDS` old or more."""
        last = self._inbound.get((instance_id, destination_subject(is_dm=is_dm, key=key)))
        if last is None or int(self.clock()) - last[1] >= UNTIED_REPLY_HORIZON_SECONDS:
            return None
        return last

    # Visibility.

    def note_refusal(self, instance_id: str, label: str, refusal: GuardRefused) -> None:
        """Remember a refusal for the plugin status and log it (once a minute per
        instance and scope, so a flooding plugin does not flood the log)."""
        now = self.clock()
        hits = self._hits.setdefault(instance_id, {"total": 0, "by_scope": {}, "last": None})
        hits["total"] += 1
        hits["by_scope"][refusal.scope] = hits["by_scope"].get(refusal.scope, 0) + 1
        hits["last"] = {"scope": refusal.scope, "at": int(now)}
        key = (instance_id, refusal.scope)
        if now - self._last_logged.get(key, float("-inf")) >= _HIT_LOG_INTERVAL:
            self._last_logged[key] = now
            logger.warning(
                "Plugin %s (%s): send refused by the %s guard (%s), %d refused since start",
                label,
                instance_id,
                refusal.scope,
                refusal,
                hits["total"],
            )

    def hits(self, instance_id: str) -> dict[str, Any]:
        """What the guards refused for this instance since the process started:
        `total`, `by_scope` and `last` (`scope`, `at`); `{}` when nothing was refused."""
        hits = self._hits.get(instance_id)
        if not hits:
            return {}
        return {"total": hits["total"], "by_scope": dict(hits["by_scope"]), "last": hits["last"]}

    def forget(self, instance_id: str) -> None:
        self._hits.pop(instance_id, None)
        for key in [k for k in self._inbound if k[0] == instance_id]:
            del self._inbound[key]
        for key in [k for k in self._last_logged if k[0] == instance_id]:
            del self._last_logged[key]


send_guard = SendGuard()
