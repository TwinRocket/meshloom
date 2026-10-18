"""The notification dispatcher: rules, one bounded queue and one worker per channel.

``emit(event)`` evaluates the rules in memory (``rules.py``) and puts one job per
target channel in that channel's ``asyncio.Queue(maxsize=QUEUE_MAX)`` with
``put_nowait``: it never waits on a send. Each channel has one worker task
(``app.background_tasks.spawn``), started when a job arrives and ended when the
queue is empty, that sends one job at a time, so a slow or failing channel only
delays itself.

- **Limiter** (minimal, MSHL-109; token buckets and the global cap come with
  MSHL-125): one send at a time per channel and a minimum gap between two sends
  (``min_interval_s``: per preset, else per type, from the registry). Beyond
  ``MAX_PENDING`` waiting jobs a new job is folded (``aggregated``) and the worker
  sends one "+N events" summary once the queue is empty. Urgent jobs are never
  folded; a full queue drops the job (``rate_limited``). ``contact.first_seen`` is
  capped at ``FIRST_SEEN_PER_HOUR`` per channel (``aiolimiter``); the rest are
  summed into one "+N new contacts" job when the budget frees a slot.
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
tests drive retries and spacing on a fake clock. ``app.notify.dispatch_system_event``
is the legacy adapter that feeds this module until the producers are rewired
(MSHL-112).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
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
from app.notifications.events import NotificationEvent, event_from_payload
from app.notifications.transports.base import Delivery, SendResult, Transport

logger = logging.getLogger(__name__)

QUEUE_MAX = 200
MAX_PENDING = 20
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
    "failed", "rate_limited", "aggregated", "suppressed_quiet", "paused", "aborted"
]


def default_transport(channel: Channel) -> Transport | None:
    from app.notifications.transports.apprise import AppriseTransport
    from app.notifications.transports.email import EmailTransport
    from app.notifications.transports.legacy import (
        LegacyAppriseTransport,
        LegacyEmailTransport,
        LegacyWebhookTransport,
    )
    from app.notifications.transports.web_push import WebPushTransport
    from app.notifications.transports.webhook import WebhookTransport

    if channel.type == "web_push":
        return WebPushTransport()
    if channel.id not in LEGACY_CHANNEL_TYPES:
        native = {
            "email": EmailTransport,
            "webhook": WebhookTransport,
            "apprise": AppriseTransport,
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
    folded: int = 0
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
        max_pending: int = MAX_PENDING,
        first_seen_per_hour: int = FIRST_SEEN_PER_HOUR,
        first_seen_period_s: float = 3600.0,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._wall = wall_clock
        self._transport_for = transport_for
        self._queue_max = queue_max
        self._max_pending = max_pending
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
    ) -> None:
        """Queue ``event`` for each channel the rules give it, or for ``targets``
        when the caller already decided (the legacy adapter). Never raises, never
        waits on a send."""
        try:
            await self._emit(event, payload=payload, targets=targets)
        except Exception:
            logger.exception("notification dispatch failed")

    async def _emit(
        self,
        event: NotificationEvent | None,
        *,
        payload: dict[str, Any] | None,
        targets: Iterable[str] | None,
    ) -> None:
        if payload is None:
            if event is None:
                return
            payload = event.to_payload()
        if event is None:
            event = event_from_payload(payload)
        name = event.event if event is not None else str(payload.get("event") or "notification")
        urgent = bool(event.urgent if event is not None else payload.get("urgent"))
        rules = await rules_mod.load_rules()
        if targets is None:
            if event is None:
                return
            channel_ids = rules_mod.event_channel_ids(
                rules, event, is_public_or_hashtag=await _is_open_channel(event)
            )
        else:
            channel_ids = list(targets)
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
        for target in decided:
            if target.paused:
                self._journal(target.channel_id, name, "paused", dedup_key=dedup_key)
                continue
            job = _Job(delivery, urgent=urgent, dedup_key=dedup_key)
            if name == "contact.first_seen" and not urgent:
                worker = self._worker(target.channel_id)
                if not worker.first_seen.has_capacity():
                    self._hold_first_seen(worker, job)
                    continue
                await worker.first_seen.acquire()  # has capacity: returns at once
            self._enqueue(target.channel_id, job)

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
        if not job.urgent and worker.queue.qsize() >= self._max_pending:
            worker.folded += 1
            self._journal(channel_id, job.delivery.name, "aggregated", dedup_key=job.dedup_key)
        else:
            try:
                worker.queue.put_nowait(job)
            except asyncio.QueueFull:
                self._journal(
                    channel_id, job.delivery.name, "rate_limited", dedup_key=job.dedup_key
                )
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
            pass
        if worker.folded:
            count, worker.folded = worker.folded, 0
            return _Job(_grouped(GROUPED_EVENT, count))
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
        if worker.folded:
            worker.folded = 0
            self._journal(worker.channel_id, GROUPED_EVENT, "aborted")
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

        rows, self._journal_rows = self._journal_rows, []
        if not rows:
            return
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
        try:
            for (channel_id, hour), count in sent.items():
                await NotificationJournalRepository.add_sent(channel_id, hour, count)
            for channel_id, at in ok_at.items():
                await NotificationChannelRepository.set_last_ok_at(channel_id, at)
        except Exception:
            logger.debug("notification counters not written", exc_info=True)

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
) -> None:
    await notification_dispatcher().emit(event, payload=payload, targets=targets)


async def notification_statuses() -> dict[str, dict[str, Any]]:
    """The health payload's ``notification_statuses`` (never raises)."""
    try:
        return await notification_dispatcher().statuses()
    except Exception:
        logger.debug("notification statuses unavailable", exc_info=True)
        return {}
