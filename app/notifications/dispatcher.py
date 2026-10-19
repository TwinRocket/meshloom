"""The notification dispatcher: rules, one bounded queue and one worker per channel.

``emit(event)`` evaluates the rules in memory (``rules.py``) and puts one job per
target channel in that channel's ``asyncio.Queue(maxsize=QUEUE_MAX)`` with
``put_nowait``: it never waits on a send. Each channel has one worker task
(``app.background_tasks.spawn``), started when a job arrives and ended when the
queue is empty, that sends one job at a time, so a slow or failing channel only
delays itself.

- **Limits** (MSHL-125, ``limits.py``): one send at a time per channel and a minimum
  gap between two sends (``min_interval_s``). A job is admitted at ``emit`` time, in
  this order: persisted dedup key (``DEDUP_TTL_S``), the global cap
  (``limits.global_per_hour`` of the rules, default 600: over it the job is recorded
  ``rate_limited`` and ``notifications_capped`` is raised in health), the channel's
  daily quota (``quota_exceeded``, until the next UTC midnight), then the channel's
  minute and hour buckets (``aiolimiter``, ``has_capacity()`` then ``acquire()``, never
  waiting). A full bucket holds the job (``aggregated``) for ``AGGREGATION_WINDOW_S``,
  then one summary per channel ("+12 messages: 12 in #paris, 2 DMs", counts only, never
  text) takes its place in the queue once the buckets have room. An urgent job takes a
  token like any other but passes a full bucket once per (channel, dedup key). A full
  queue drops the job (``rate_limited``). ``contact.first_seen`` is also capped at
  ``FIRST_SEEN_PER_HOUR`` per channel; the rest are summed into one "+N new contacts" job
  when the budget frees a slot.
- **Retries** (``tenacity``): ``RETRY_WAITS`` (0 s, 5 s, 30 s) only while the
  transport answers ``retry`` (5xx, timeout, network error, 429 whose
  ``Retry-After`` wins, capped at ``MAX_RETRY_AFTER_S``); never after a ``failed``.
  A ``message.*`` job still failing is dropped as stale; ``radio.offline`` and
  ``telemetry.alert`` still answering ``retry`` get one more attempt
  ``LONG_RETRY_S`` later, requeued so the worker is free meanwhile; a ``failed``
  answer is final for them too.
- **Outcomes.** A success counts in memory (flushed to ``notification_counters``
  every ``COUNTER_FLUSH_S``, with the channel's ``last_ok_at``); a failure updates
  ``failure_count`` and ``last_error`` at once (``secret_fields.scrub_error``) and
  a success after failures resets them. ``notification_deliveries`` rows are
  written only for ``failed``, ``rate_limited``, ``aggregated``, ``paused`` and
  ``aborted`` (``suppressed_quiet`` comes with the quiet hours), and carry the
  event id, the hashed ``dedup_key`` and a code: never the message text.
- **Status.** ``notification_statuses()`` is the health payload's map; a change of
  a channel's status or error broadcasts ``health``.
- **Shutdown.** ``drain_background_tasks`` cancels the workers, the long retries
  and the first-seen flushers; each records what it still held as ``aborted``,
  and the counter loop flushes once more.

The clock and the sleep are injected (``Dispatcher(clock=..., sleep=...)``): the
tests drive retries and spacing on a fake clock. The producers emit through
``app/notifications/producers.py``, never to a medium directly.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from aiolimiter import AsyncLimiter
from tenacity import AsyncRetrying, RetryCallState, retry_if_result, stop_after_attempt

from app.notifications import rules as rules_mod
from app.notifications.channels import (
    BUILTIN_PUSH_ID,
    Channel,
    min_interval_s,
)
from app.notifications.events import MessageData, NotificationEvent, event_from_payload
from app.notifications.limits import (
    DEDUP_PREFIX,
    GLOBAL_PER_HOUR,
    URGENT_PASS_TTL_S,
    ChannelBuckets,
    ClockedLimiter,
    DedupStore,
    dedup_ttl,
    effective_limits,
)
from app.notifications.transports.base import Delivery, SendResult, Transport

logger = logging.getLogger(__name__)

QUEUE_MAX = 200
AGGREGATION_WINDOW_S = 60.0
STATE_PRUNE_S = 3600.0
FIRST_SEEN_PER_HOUR = 10
RETRY_WAITS = (0.0, 5.0, 30.0)
MAX_RETRY_AFTER_S = 120.0
LONG_RETRY_S = 300.0
LONG_RETRY_EVENTS = frozenset({"radio.offline", "telemetry.alert"})
COUNTER_FLUSH_S = 60.0

GROUPED_EVENT = "notifications_grouped"
FIRST_SEEN_GROUPED_EVENT = "first_seen_grouped"

# The 4.18.x media, fed by the legacy adapter. Their destinations still live in
# ``app_settings.notification_destinations``, so they are delivered even when the
# import (migration 113) created no row for them.
LEGACY_CHANNEL_TYPES = {
    BUILTIN_PUSH_ID: "web_push",
    "legacy:email": "email",
    "legacy:webhook": "webhook",
    "legacy:apprise": "apprise",
}
LEGACY_CHANNEL_NAMES = {
    BUILTIN_PUSH_ID: "Web Push",
    "legacy:email": "Email",
    "legacy:webhook": "Webhook",
    "legacy:apprise": "Apprise",
}

# ``secret_unreadable`` joins this list with the encryption at rest of the secrets.
ChannelStatus = Literal["connected", "error", "paused", "disconnected"]
DeliveryStatus = Literal[
    "failed",
    "rate_limited",
    "quota_exceeded",
    "aggregated",
    "suppressed_quiet",
    "paused",
    "aborted",
]


def default_transport(channel: Channel) -> Transport | None:
    from app.notifications.transports.apprise import AppriseTransport
    from app.notifications.transports.discord import DiscordTransport
    from app.notifications.transports.email import EmailTransport
    from app.notifications.transports.legacy import (
        LegacyAppriseTransport,
        LegacyEmailTransport,
        LegacyWebhookTransport,
    )
    from app.notifications.transports.ntfy import NtfyTransport
    from app.notifications.transports.web_push import WebPushTransport
    from app.notifications.transports.webhook import WebhookTransport

    if channel.type == "web_push":
        return WebPushTransport()
    if channel.id not in LEGACY_CHANNEL_TYPES:
        native = {
            "email": EmailTransport,
            "discord": DiscordTransport,
            "webhook": WebhookTransport,
            "apprise": AppriseTransport,
            "ntfy": NtfyTransport,
        }.get(channel.type)
        if native:
            return native()
    legacy = {
        "legacy:email": LegacyEmailTransport,
        "legacy:webhook": LegacyWebhookTransport,
        "legacy:apprise": LegacyAppriseTransport,
    }.get(channel.id)
    return legacy() if legacy else None


@dataclass
class _Job:
    delivery: Delivery
    urgent: bool = False
    dedup_key: str = ""
    # The one long retry of radio.offline / telemetry.alert: a single attempt.
    long_retry: bool = False
    attempts_before: int = 0
    # A Public or ``#`` channel message: its name may appear in a summary.
    is_open: bool = False


@dataclass
class _Held:
    """What a channel's full bucket held back, as counts only (never a text)."""

    count: int = 0
    dms: int = 0
    private: int = 0
    other: int = 0
    channels: dict[str, int] = field(default_factory=dict)

    def add(self, job: _Job) -> None:
        self.count += 1
        event = job.delivery.event
        if event is None or not event.event.startswith("message."):
            self.other += 1
        elif event.event == "message.dm":
            self.dms += 1
        else:
            name = (
                str(event.data.channel_name or "")[:40]
                if isinstance(event.data, MessageData)
                else ""
            )
            if job.is_open and name:
                self.channels[name] = self.channels.get(name, 0) + 1
            else:
                self.private += 1

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {"event": GROUPED_EVENT, "count": self.count}
        if self.dms:
            out["dms"] = self.dms
        if self.private:
            out["private"] = self.private
        if self.other:
            out["others"] = self.other
        if self.channels:
            top = sorted(self.channels.items(), key=lambda kv: (-kv[1], kv[0]))
            out["channels"] = dict(top[:3])
            rest = sum(n for _, n in top[3:])
            if rest:
                out["private"] = out.get("private", 0) + rest
        return out


@dataclass
class _Live:
    """What the dispatcher saw of a channel since start (overlays its row)."""

    failure_count: int | None = None
    last_error: str | None = None
    last_ok_at: int | None = None
    reported: tuple[str, str | None] | None = None


@dataclass
class _Worker:
    channel_id: str
    queue: asyncio.Queue[_Job]
    first_seen: AsyncLimiter
    task: asyncio.Task[None] | None = None
    current: _Job | None = None
    buckets: ChannelBuckets | None = None
    held: _Held = field(default_factory=_Held)
    agg_flusher: asyncio.Task[None] | None = None
    first_seen_held: int = 0
    first_seen_flusher: asyncio.Task[None] | None = None
    last_send_at: float | None = None
    long_retries: set[asyncio.Task[None]] = field(default_factory=set)


class Dispatcher:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        wall_clock: Callable[[], float] = time.time,
        transport_for: Callable[[Channel], Transport | None] = default_transport,
        queue_max: int = QUEUE_MAX,
        aggregation_window_s: float = AGGREGATION_WINDOW_S,
        first_seen_per_hour: int = FIRST_SEEN_PER_HOUR,
        first_seen_period_s: float = 3600.0,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._wall = wall_clock
        self._transport_for = transport_for
        self._queue_max = queue_max
        self._agg_window_s = aggregation_window_s
        self._dedup = DedupStore(wall_clock)
        self._cap: ClockedLimiter | None = None
        self._cap_rate = 0
        self._capped = False
        self._last_prune = 0.0
        self._first_seen_per_hour = first_seen_per_hour
        self._first_seen_period_s = first_seen_period_s
        self._workers: dict[str, _Worker] = {}
        self._live: dict[str, _Live] = {}
        self._sent: dict[tuple[str, int], int] = {}
        self._ok_at: dict[str, int] = {}
        self._counter_task: asyncio.Task[None] | None = None
        self._journal_rows: list[dict[str, Any]] = []
        self._journal_task: asyncio.Task[None] | None = None

    # -- emit -------------------------------------------------------------------

    async def emit(
        self,
        event: NotificationEvent | None,
        *,
        payload: dict[str, Any] | None = None,
        targets: Iterable[str] | None = None,
        exclude: Iterable[str] = (),
        rules: Mapping[str, Any] | None = None,
        is_open: bool | None = None,
        muted: bool = False,
        blocked: bool = False,
    ) -> int:
        """Queue ``event`` for each channel the rules give it (minus ``exclude``, the
        channels a producer rules out, such as a blocked sender's message), or for
        ``targets`` when the caller already decided. A producer that already loaded the
        rules or knows whether the conversation is a Public or ``#`` channel passes them
        (``rules``, ``is_open``) to spare the lookups, and tells whether the conversation is
        muted or the sender blocked (``muted``, ``blocked``: ``rules.event_channel_ids``). Returns how many channels it queued
        the event for (paused, disabled and unknown channels do not count). Never raises,
        never waits on a send."""
        try:
            return await self._emit(
                event,
                payload=payload,
                targets=targets,
                exclude=frozenset(exclude),
                rules=rules,
                is_open=is_open,
                muted=muted,
                blocked=blocked,
            )
        except Exception:
            logger.exception("notification dispatch failed")
            return 0

    async def _emit(
        self,
        event: NotificationEvent | None,
        *,
        payload: dict[str, Any] | None,
        targets: Iterable[str] | None,
        exclude: frozenset[str] = frozenset(),
        rules: Mapping[str, Any] | None = None,
        is_open: bool | None = None,
        muted: bool = False,
        blocked: bool = False,
    ) -> int:
        if payload is None:
            if event is None:
                return 0
            payload = event.to_payload()
        if event is None:
            event = event_from_payload(payload)
        name = event.event if event is not None else str(payload.get("event") or "notification")
        urgent = bool(event.urgent if event is not None else payload.get("urgent"))
        if rules is None:
            rules = await rules_mod.load_rules()
        if is_open is None and event is not None and event.event.startswith("message."):
            is_open = await _is_open_channel(event)
        if targets is None:
            if event is None:
                return 0
            channel_ids = rules_mod.event_channel_ids(
                rules, event, is_public_or_hashtag=bool(is_open), muted=muted, blocked=blocked
            )
        else:
            channel_ids = list(targets)
        channel_ids = [c for c in channel_ids if c not in exclude]
        channels = await self._channels(channel_ids, allow_legacy=targets is not None)
        decided = rules_mod.apply_pauses(
            rules,
            [c.id for c in channels if c.enabled],
            name=name,
            urgent=urgent,
            channel_paused_until={c.id: c.paused_until for c in channels},
            now=self._wall(),
        )
        delivery = Delivery(name=name, payload=dict(payload), event=event)
        dedup_key = event.dedup_key if event is not None else ""
        by_id = {c.id: c for c in channels}
        queued = 0
        for target in decided:
            if target.paused:
                self._journal(target.channel_id, name, "paused", dedup_key=dedup_key)
                continue
            job = _Job(delivery, urgent=urgent, dedup_key=dedup_key, is_open=bool(is_open))
            if await self._admit(by_id[target.channel_id], job, rules):
                queued += 1
        return queued

    # -- admission --------------------------------------------------------------

    def _global_cap(self, rules: Mapping[str, Any]) -> ClockedLimiter:
        limits = rules.get("limits") if isinstance(rules, Mapping) else None
        raw = limits.get("global_per_hour") if isinstance(limits, Mapping) else None
        rate = raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1 else None
        rate = rate or GLOBAL_PER_HOUR
        if self._cap is None or rate != self._cap_rate:
            self._cap, self._cap_rate = ClockedLimiter(rate, 3600.0, self._clock), rate
        return self._cap

    async def _channel_buckets(self, worker: _Worker, channel: Channel) -> ChannelBuckets:
        limits = effective_limits(channel)
        if worker.buckets is None:
            worker.buckets = ChannelBuckets(limits, self._clock, self._wall)
        else:
            worker.buckets.configure(limits)
        buckets = worker.buckets
        if buckets.needs_day_load():
            buckets.load_day(await self._sent_today(channel.id, buckets.day_start_hour))
        return buckets

    async def _sent_today(self, channel_id: str, day_start_hour: int) -> int:
        """Sends of the current UTC day: the flushed counters plus the unflushed ones."""
        from app.repository.notification_channels import NotificationJournalRepository

        total = sum(
            n
            for (cid, hour), n in self._sent.items()
            if cid == channel_id and hour >= day_start_hour
        )
        try:
            rows = await NotificationJournalRepository.hourly_counts(channel_id, day_start_hour)
            total += sum(int(row["sent"]) for row in rows)
        except Exception:
            logger.debug("notification counters unreadable", exc_info=True)
        return total

    async def _admit(self, channel: Channel, job: _Job, rules: Mapping[str, Any]) -> bool:
        """Decide what becomes of ``job`` on ``channel``: queued, held for the
        aggregation window, or dropped with a journal row. True unless dropped."""
        name, channel_id = job.delivery.name, channel.id
        worker = self._worker(channel_id)
        ttl = dedup_ttl(name) if job.dedup_key else None
        dedup_key = f"{DEDUP_PREFIX}{channel_id}:{job.dedup_key}"
        if ttl is not None:
            await self._dedup.load()
            if self._dedup.seen(dedup_key):
                return False
        cap = self._global_cap(rules)
        if not cap.has_capacity():
            self._update_capped()
            self._journal(channel_id, name, "rate_limited", dedup_key=job.dedup_key)
            return False
        buckets = await self._channel_buckets(worker, channel)
        if buckets.quota_exhausted():
            self._journal(channel_id, name, "quota_exceeded", dedup_key=job.dedup_key)
            return False
        if ttl is not None:
            self._dedup.mark(dedup_key, ttl)
            self._start_counter_loop()
        first_seen = name == "contact.first_seen" and not job.urgent
        if first_seen and not worker.first_seen.has_capacity():
            self._hold_first_seen(worker, job)
            return True
        if not buckets.has_capacity():
            pass_key = f"{DEDUP_PREFIX}urgent:{channel_id}:{job.dedup_key}"
            if not job.urgent or not job.dedup_key or self._dedup.seen(pass_key):
                self._hold(worker, job)
                return True
            # Urgent passes a full bucket once per dedup key (and still takes a token).
            self._dedup.mark(pass_key, URGENT_PASS_TTL_S)
            self._start_counter_loop()
        if first_seen:
            await worker.first_seen.acquire()  # has capacity: returns at once
        await cap.acquire()
        await buckets.take()
        self._update_capped()
        self._enqueue(channel_id, job)
        return True

    def _update_capped(self) -> None:
        capped = self._cap is not None and not self._cap.has_capacity()
        if capped == self._capped:
            return
        self._capped = capped
        if capped:
            self._start_counter_loop()  # its tick notices the end of the cap
        self._broadcast_health()

    @property
    def capped(self) -> bool:
        """The global cap is reached (health ``notifications_capped``)."""
        self._update_capped()
        return self._capped

    def _hold(self, worker: _Worker, job: _Job) -> None:
        """A full bucket: keep the job as a count for the next summary."""
        worker.held.add(job)
        self._journal(worker.channel_id, job.delivery.name, "aggregated", dedup_key=job.dedup_key)
        if worker.agg_flusher is None or worker.agg_flusher.done():
            from app.background_tasks import spawn

            worker.agg_flusher = spawn(
                self._flush_held(worker), name=f"notify-aggregate:{worker.channel_id}"
            )

    async def _flush_held(self, worker: _Worker) -> None:
        """After the window, queue one summary of what the buckets held, once the
        buckets and the global cap have room for it (else wait one more window)."""
        try:
            await self._sleep(self._agg_window_s)
            buckets = worker.buckets
            while buckets is not None and not (
                buckets.quota_exhausted()
                or (buckets.has_capacity() and (self._cap is None or self._cap.has_capacity()))
            ):
                await self._sleep(self._agg_window_s)
        except asyncio.CancelledError:
            if worker.held.count:
                worker.held = _Held()
                self._journal(worker.channel_id, GROUPED_EVENT, "aborted")
                await self._flush_journal()
            raise
        held, worker.held, worker.agg_flusher = worker.held, _Held(), None
        if buckets is not None:
            if buckets.quota_exhausted():
                self._journal(worker.channel_id, GROUPED_EVENT, "quota_exceeded")
                return
            await buckets.take()
        if self._cap is not None and self._cap.has_capacity():
            await self._cap.acquire()
        self._update_capped()
        self._enqueue(worker.channel_id, _Job(Delivery(GROUPED_EVENT, held.payload())))

    async def _channels(self, ids: list[str], *, allow_legacy: bool) -> list[Channel]:
        from app.repository.notification_channels import NotificationChannelRepository

        known: dict[str, Channel] = {}
        try:
            known = {c.id: c for c in await NotificationChannelRepository.list_all()}
        except Exception:
            logger.debug("notification channels unreadable", exc_info=True)
        resolved: list[Channel] = []
        for channel_id in dict.fromkeys(ids):
            channel = known.get(channel_id)
            if channel is None and allow_legacy and channel_id in LEGACY_CHANNEL_TYPES:
                channel = _legacy_channel(channel_id)
            if channel is None:
                if not allow_legacy:
                    # Logged once per id by the repository.
                    await NotificationChannelRepository.resolve([channel_id])
                continue
            resolved.append(channel)
        return resolved

    def _worker(self, channel_id: str) -> _Worker:
        worker = self._workers.get(channel_id)
        if worker is None:
            worker = _Worker(
                channel_id=channel_id,
                queue=asyncio.Queue(maxsize=self._queue_max),
                first_seen=AsyncLimiter(self._first_seen_per_hour, self._first_seen_period_s),
            )
            self._workers[channel_id] = worker
        return worker

    def _hold_first_seen(self, worker: _Worker, job: _Job) -> None:
        worker.first_seen_held += 1
        self._journal(worker.channel_id, job.delivery.name, "aggregated", dedup_key=job.dedup_key)
        if worker.first_seen_flusher is None or worker.first_seen_flusher.done():
            from app.background_tasks import spawn

            worker.first_seen_flusher = spawn(
                self._flush_first_seen(worker), name=f"notify-first-seen:{worker.channel_id}"
            )

    def _enqueue(self, channel_id: str, job: _Job) -> None:
        worker = self._worker(channel_id)
        try:
            worker.queue.put_nowait(job)
        except asyncio.QueueFull:
            self._journal(channel_id, job.delivery.name, "rate_limited", dedup_key=job.dedup_key)
            return
        self._start(worker)

    def _start(self, worker: _Worker) -> None:
        if worker.task is None or worker.task.done():
            from app.background_tasks import spawn

            worker.task = spawn(self._run(worker), name=f"notify-worker:{worker.channel_id}")

    async def _flush_first_seen(self, worker: _Worker) -> None:
        try:
            # Waits for the hourly budget to free a slot, then says how many were held.
            async with worker.first_seen:
                count, worker.first_seen_held = worker.first_seen_held, 0
        except asyncio.CancelledError:
            if worker.first_seen_held:
                self._journal(worker.channel_id, FIRST_SEEN_GROUPED_EVENT, "aborted")
                await self._flush_journal()
            raise
        self._enqueue(worker.channel_id, _Job(_grouped(FIRST_SEEN_GROUPED_EVENT, count)))

    # -- worker -----------------------------------------------------------------

    def _next(self, worker: _Worker) -> _Job | None:
        try:
            return worker.queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def _run(self, worker: _Worker) -> None:
        try:
            while (job := self._next(worker)) is not None:
                worker.current = job
                found = await self._channel_for_send(worker.channel_id)
                if found is not None:  # else deleted since it was queued
                    await self._space(worker, found[0])
                    await self._deliver(worker, job, *found)
                worker.current = None
        except asyncio.CancelledError:
            await self._abort(worker)
            raise

    async def _space(self, worker: _Worker, channel: Channel) -> None:
        interval = min_interval_s(channel.type, channel.preset)
        if worker.last_send_at is not None and interval > 0:
            wait = worker.last_send_at + interval - self._clock()
            if wait > 0:
                await self._sleep(wait)
        worker.last_send_at = self._clock()

    async def _channel_for_send(self, channel_id: str) -> tuple[Channel, dict[str, str]] | None:
        from app.repository.notification_channels import NotificationChannelRepository

        try:
            found = await NotificationChannelRepository.get_for_send(channel_id)
        except Exception:
            logger.debug("notification channel unreadable", exc_info=True)
            found = None
        if found is None and channel_id in LEGACY_CHANNEL_TYPES:
            return _legacy_channel(channel_id), {}
        return found

    async def _deliver(
        self, worker: _Worker, job: _Job, channel: Channel, secrets: dict[str, str]
    ) -> None:
        transport = self._transport_for(channel)
        if transport is None:
            await self._failed(channel, job, SendResult("failed", error_code="no_transport"), 1)
            return

        async def attempt() -> SendResult:
            try:
                return await transport.send(channel, None, job.delivery, secrets)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The type only: an exception text may carry a URL and its token.
                logger.warning(
                    "notification transport %s raised %s", channel.type, type(exc).__name__
                )
                return SendResult("failed", error_code="send_failed")

        retrying = AsyncRetrying(
            stop=stop_after_attempt(1 if job.long_retry else len(RETRY_WAITS)),
            wait=_retry_wait,
            retry=retry_if_result(lambda result: result.status == "retry"),
            retry_error_callback=lambda state: state.outcome.result() if state.outcome else None,
            sleep=self._sleep,
        )
        result: SendResult = await retrying(attempt)
        attempts = job.attempts_before + int(retrying.statistics.get("attempt_number", 1))
        if result.ok:
            await self._succeeded(channel)
            return
        # Only a retryable failure earns the long retry: a ``failed`` answer (a 4xx,
        # a refused destination, an Apprise URL that failed after others took the
        # alert) is final, and another attempt would resend to who already got it.
        if (
            result.status == "retry"
            and job.delivery.name in LONG_RETRY_EVENTS
            and not job.long_retry
        ):
            await self._record_failure(channel, result.error_code, secrets)
            self._schedule_long_retry(worker, job, attempts)
            return
        await self._failed(channel, job, result, attempts, secrets)

    def _schedule_long_retry(self, worker: _Worker, job: _Job, attempts: int) -> None:
        from app.background_tasks import spawn

        retry = _Job(
            job.delivery,
            urgent=True,  # never folded into a summary
            dedup_key=job.dedup_key,
            long_retry=True,
            attempts_before=attempts,
        )

        async def later() -> None:
            try:
                await self._sleep(LONG_RETRY_S)
            except asyncio.CancelledError:
                self._journal(
                    worker.channel_id,
                    retry.delivery.name,
                    "aborted",
                    dedup_key=retry.dedup_key,
                    attempts=attempts,
                )
                await self._flush_journal()
                raise
            self._enqueue(worker.channel_id, retry)

        task = spawn(later(), name=f"notify-long-retry:{worker.channel_id}")
        worker.long_retries.add(task)
        task.add_done_callback(worker.long_retries.discard)

    async def _abort(self, worker: _Worker) -> None:
        held: list[_Job] = [worker.current] if worker.current is not None else []
        worker.current = None
        while True:
            try:
                held.append(worker.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        for job in held:
            self._journal(worker.channel_id, job.delivery.name, "aborted", dedup_key=job.dedup_key)
        await self._flush_journal()

    # -- outcomes ---------------------------------------------------------------

    async def _succeeded(self, channel: Channel) -> None:
        now = int(self._wall())
        key = (channel.id, now // 3600)
        self._sent[key] = self._sent.get(key, 0) + 1
        self._ok_at[channel.id] = now
        live = self._live.setdefault(channel.id, _Live())
        failing = (
            live.failure_count if live.failure_count is not None else channel.failure_count
        ) > 0
        live.last_ok_at = now
        if failing:
            live.failure_count, live.last_error = 0, None
            await self._write_status(channel.id, ok=True)
        self._start_counter_loop()
        self._report(channel)

    async def _record_failure(
        self, channel: Channel, code: str | None, secrets: dict[str, str] | None = None
    ) -> None:
        from app.secret_fields import scrub_error

        error = scrub_error(code or "send_failed", (secrets or {}).values())
        live = self._live.setdefault(channel.id, _Live())
        base = live.failure_count if live.failure_count is not None else channel.failure_count
        live.failure_count, live.last_error = base + 1, error
        await self._write_status(channel.id, ok=False, error=error)
        self._report(channel)

    async def _failed(
        self,
        channel: Channel,
        job: _Job,
        result: SendResult,
        attempts: int,
        secrets: dict[str, str] | None = None,
    ) -> None:
        await self._record_failure(channel, result.error_code, secrets)
        live = self._live[channel.id]
        stale = " (stale, dropped)" if job.delivery.name.startswith("message.") else ""
        logger.warning(
            "Notification %s to %s failed after %d attempt(s): %s%s",
            job.delivery.name,
            channel.id,
            attempts,
            live.last_error,
            stale,
        )
        self._journal(
            channel.id,
            job.delivery.name,
            "failed",
            dedup_key=job.dedup_key,
            error=live.last_error,
            attempts=attempts,
        )
        await self._flush_journal()

    async def _write_status(self, channel_id: str, *, ok: bool, error: str | None = None) -> None:
        from app.repository.notification_channels import NotificationChannelRepository

        try:
            await NotificationChannelRepository.record_result(channel_id, ok=ok, error=error)
        except Exception:
            logger.debug("notification status not written", exc_info=True)

    def _report(self, channel: Channel) -> None:
        """Broadcast ``health`` when this channel's status or error changed."""
        live = self._live.setdefault(channel.id, _Live())
        status = _status_of(channel, live, self._wall())
        current = (status, live.last_error if live.last_error is not None else None)
        if live.reported == current:
            return
        first = live.reported is None
        live.reported = current
        if first and status == "connected":
            return  # nothing changed from what health already said
        self._broadcast_health()

    def _broadcast_health(self) -> None:
        try:
            from app.services.radio_runtime import radio_runtime
            from app.websocket import broadcast_health

            broadcast_health(radio_runtime.is_connected, radio_runtime.connection_info)
        except Exception:
            logger.debug("health broadcast failed", exc_info=True)

    # -- journal and counters ---------------------------------------------------

    def _journal(
        self,
        channel_id: str,
        event: str,
        status: DeliveryStatus,
        *,
        dedup_key: str = "",
        error: str | None = None,
        attempts: int = 1,
    ) -> None:
        """Queue one deliveries row; rows are written together, soon after."""
        self._journal_rows.append(
            {
                "channel_id": channel_id,
                "event": event,
                "status": status,
                "dedup_key": dedup_key,
                "error": error,
                "attempts": attempts,
            }
        )
        if self._journal_task is None or self._journal_task.done():
            from app.background_tasks import spawn

            self._journal_task = spawn(self._flush_journal(), name="notify-journal")

    async def _flush_journal(self) -> None:
        from app.repository.notification_channels import NotificationJournalRepository

        # Rows queued while a write is in flight are written by the same task.
        while self._journal_rows:
            rows, self._journal_rows = self._journal_rows, []
            try:
                await NotificationJournalRepository.add_deliveries(rows)
            except Exception:
                logger.debug("notification journal not written", exc_info=True)

    def _start_counter_loop(self) -> None:
        if self._counter_task is None or self._counter_task.done():
            from app.background_tasks import spawn

            self._counter_task = spawn(self._counter_loop(), name="notify-counters")

    async def _counter_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(COUNTER_FLUSH_S)
                await self.flush_counters()
                self._update_capped()
        finally:
            await self.flush_counters()

    async def flush_counters(self) -> None:
        """Write the in-memory success counts and each channel's last success."""
        from app.repository.notification_channels import (
            NotificationChannelRepository,
            NotificationJournalRepository,
        )

        sent, self._sent = self._sent, {}
        ok_at, self._ok_at = self._ok_at, {}
        await self._dedup.flush()
        if self._wall() - self._last_prune >= STATE_PRUNE_S:
            self._last_prune = self._wall()
            try:
                await NotificationJournalRepository.state_prune(int(self._wall()))
            except Exception:
                logger.debug("notification state not pruned", exc_info=True)
        try:
            for (channel_id, hour), count in sent.items():
                await NotificationJournalRepository.add_sent(channel_id, hour, count)
            for channel_id, at in ok_at.items():
                await NotificationChannelRepository.set_last_ok_at(channel_id, at)
        except Exception:
            logger.debug("notification counters not written", exc_info=True)

    # -- test -------------------------------------------------------------------

    async def send_test(
        self, channel: Channel, secrets: dict[str, str], *, record: bool = False
    ) -> SendResult:
        """One ``notify.test`` send through the channel's transport (so through the
        egress guard), outside the queue: no retry, no journal row, no counter. With
        ``record`` (a saved channel) the answer also updates its status. Never raises."""
        from app.notifications.events import TestData, TestEvent, dedup_hash

        transport = self._transport_for(channel)
        if transport is None:
            return SendResult("failed", error_code="no_transport")
        event = TestEvent(
            event="notify.test",
            dedup_key=dedup_hash("notify.test", time.time_ns()),
            data=TestData(),
        )
        delivery = Delivery(name=event.event, payload=event.to_payload(), event=event)
        try:
            result = await transport.send(channel, None, delivery, secrets)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("notification test on %s raised %s", channel.type, type(exc).__name__)
            result = SendResult("failed", error_code="send_failed")
        if record:
            if result.ok:
                live = self._live.setdefault(channel.id, _Live())
                failing = (
                    live.failure_count if live.failure_count is not None else channel.failure_count
                ) > 0
                live.last_ok_at = int(self._wall())
                if failing:
                    live.failure_count, live.last_error = 0, None
                    await self._write_status(channel.id, ok=True)
                self._report(channel)
            else:
                await self._record_failure(channel, result.error_code, secrets)
        return result

    # -- status -----------------------------------------------------------------

    async def statuses(self) -> dict[str, dict[str, Any]]:
        """``{channel id: {status, last_error, last_ok_at}}`` for the health payload."""
        from app.repository.notification_channels import NotificationChannelRepository

        try:
            channels = await NotificationChannelRepository.list_all()
        except Exception:
            channels = []
        now = self._wall()
        out: dict[str, dict[str, Any]] = {}
        seen = {c.id for c in channels}
        extra = [_legacy_channel(cid) for cid in self._live if cid not in seen]
        for channel in [*channels, *extra]:
            live = self._live.get(channel.id) or _Live()
            last_error = live.last_error if live.failure_count is not None else channel.last_error
            out[channel.id] = {
                "status": _status_of(channel, live, now),
                "last_error": last_error,
                "last_ok_at": live.last_ok_at or channel.last_ok_at,
            }
        return out

    async def wait_idle(self) -> None:
        """Until every queue is empty and every worker stopped (tests)."""
        while True:
            tasks = [w.task for w in self._workers.values() if w.task and not w.task.done()]
            if self._journal_task is not None and not self._journal_task.done():
                tasks.append(self._journal_task)
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)


def _grouped(name: str, count: int) -> Delivery:
    return Delivery(name=name, payload={"event": name, "count": count})


def _legacy_channel(channel_id: str) -> Channel:
    return Channel(
        id=channel_id,
        type=LEGACY_CHANNEL_TYPES[channel_id],
        name=LEGACY_CHANNEL_NAMES[channel_id],
        deletable=channel_id != BUILTIN_PUSH_ID,
    )


def _status_of(channel: Channel, live: _Live, now: float) -> ChannelStatus:
    if not channel.enabled:
        return "disconnected"
    if channel.paused_until is not None and channel.paused_until > now:
        return "paused"
    failures = live.failure_count if live.failure_count is not None else channel.failure_count
    return "error" if failures > 0 else "connected"


def _retry_wait(state: RetryCallState) -> float:
    result = state.outcome.result() if state.outcome and not state.outcome.failed else None
    if isinstance(result, SendResult) and result.retry_after is not None:
        return min(result.retry_after, MAX_RETRY_AFTER_S)
    return RETRY_WAITS[min(state.attempt_number, len(RETRY_WAITS) - 1)]


async def _is_open_channel(event: NotificationEvent) -> bool:
    """A Public or ``#`` channel (its messages follow ``message.channel``)."""
    if event.event == "message.dm" or not event.conversation_key:
        return False
    from app.channel_constants import is_public_channel_key
    from app.repository.channels import ChannelRepository

    if is_public_channel_key(event.conversation_key):
        return True
    try:
        channel = await ChannelRepository.get_by_key(event.conversation_key)
    except Exception:
        return False
    return bool(channel is not None and channel.is_hashtag)


_instance: Dispatcher | None = None
_instance_loop: asyncio.AbstractEventLoop | None = None


def notification_dispatcher() -> Dispatcher:
    """The process-wide dispatcher, bound to the running loop (a new loop, as in
    each test, gets a new one: queues and limiters belong to one loop)."""
    global _instance, _instance_loop
    loop = asyncio.get_running_loop()
    if _instance is None or _instance_loop is not loop:
        _instance, _instance_loop = Dispatcher(), loop
    return _instance


async def emit(
    event: NotificationEvent | None,
    *,
    payload: dict[str, Any] | None = None,
    targets: Iterable[str] | None = None,
    exclude: Iterable[str] = (),
    rules: Mapping[str, Any] | None = None,
    is_open: bool | None = None,
    muted: bool = False,
    blocked: bool = False,
) -> int:
    return await notification_dispatcher().emit(
        event,
        payload=payload,
        targets=targets,
        exclude=exclude,
        rules=rules,
        is_open=is_open,
        muted=muted,
        blocked=blocked,
    )


def notifications_capped() -> bool:
    """The health payload's ``notifications_capped`` (never raises)."""
    try:
        return bool(_instance is not None and _instance.capped)
    except Exception:
        return False


async def notification_statuses() -> dict[str, dict[str, Any]]:
    """The health payload's ``notification_statuses`` (never raises)."""
    try:
        return await notification_dispatcher().statuses()
    except Exception:
        logger.debug("notification statuses unavailable", exc_info=True)
        return {}
