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
  ``telemetry.alert`` and the quiet-hours / pause digest still answering ``retry`` get one more attempt
  ``LONG_RETRY_S`` later, requeued so the worker is free meanwhile; a ``failed``
  answer is final for them too.
- **Outcomes.** A success counts in memory (flushed to ``notification_counters``
  every ``COUNTER_FLUSH_S``, with the channel's ``last_ok_at``); a failure updates
  ``failure_count`` and ``last_error`` at once (``secret_fields.scrub_error``) and
  a success after failures resets them. ``notification_deliveries`` rows are
  written only for ``failed``, ``rate_limited``, ``aggregated``, ``paused``,
  ``aborted``, ``suppressed_quiet`` (quiet hours, ``quiet_hours.py``), and carry the
  event id, the hashed ``dedup_key`` and a code: never the message text.
- **A channel that keeps failing** (MSHL-130, ``failures.py``): from the third
  consecutive failure ``notify.channel_failed`` goes to every other enabled channel, once
  a day per channel; from the twentieth the channel is paused (``paused_until``) for 5 min,
  15 min, 1 h, then 6 h, and ``probe_tick`` (every ``QUIET_TICK_S``) sends it a test once
  the pause ran out: a success resumes it, a failure pauses it for the next step.
  ``resume`` (the Resume button) clears the pause and the failures.
- **Admin-only notices** (MSHL-399, MSHL-407): ``notify.channel_changed``,
  ``notify.channel_failed``, ``notify.destination_changed`` and ``notify.heartbeat`` reach on
  Web Push only the devices of administrators (``user_rules.ADMIN_ONLY_EVENTS``); without one,
  or when the owners cannot be read, Web Push is left out. ``send_test`` on Web Push reaches
  the requester's devices only (the probe of a paused channel: the administrators').
  ``notify.channel_failed`` is urgent: quiet hours and the global pause never hold it.
- **Keyword alerts and watched nodes on Web Push** (MSHL-400): ``message.keyword``,
  ``contact.node_seen`` and ``contact.node_back`` come from instance rules the admin manages,
  so ``builtin:push`` reaches administrators' devices only (``user_rules.INSTANCE_RULE_EVENTS``),
  minus an account paused, with that conversation off, or already getting the message; no
  administrator device, or owners unreadable: Web Push is left out.
- **Status.** ``notification_statuses()`` is the health payload's map and
  ``notifications_failing()`` its flag (a channel failing, paused or with a secret the key
  cannot open); a change of a channel's status or error broadcasts ``health``.
- **WebSocket frame** (MSHL-138, ``ws_frame.py``): an event that matched at least one
  enabled channel is broadcast as ``notification_event`` (typed data, no rendered text),
  before the pauses and quiet hours, unless ``rules.emit_ws_events`` is false. A channel
  message that names the radio is framed as ``message.mention`` whichever channels got the
  mention, and the last ``FRAME_DEDUP_MAX`` ``(event, dedup_key)`` sent are not sent again
  (MSHL-402).
- **Heartbeat** (MSHL-131, ``heartbeat.py``): ``self.heartbeats`` runs one loop per channel
  with ``heartbeat_interval_s``, sending ``notify.heartbeat`` (or pinging ``heartbeat_url``)
  outside the rules, the pauses, quiet hours and the buckets; ``start`` and every quiet tick
  reconcile the loops with the channels.
- **Digest mode** (MSHL-136, ``digest_interval_s``): a channel with an interval counts its digestible
  events (``quiet.digestible``: not urgent, not ``always_deliver``, not a mention, keyword or watched
  node) under ``digest:`` counters and a ``digest_due:`` row, and ``digest_tick`` (the minute loop)
  sends one summary per window; see ``app/notifications/AGENTS.md``, "Digest mode".
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
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from aiolimiter import AsyncLimiter
from tenacity import AsyncRetrying, RetryCallState, retry_if_result, stop_after_attempt

from app.notifications import failures, ws_frame
from app.notifications import quiet_hours as quiet
from app.notifications import rules as rules_mod
from app.notifications.channels import (
    BUILTIN_PUSH_ID,
    Channel,
    min_interval_s,
)
from app.notifications.events import MessageData, NotificationEvent, event_from_payload
from app.notifications.limits import (
    DEDUP_MAX_ENTRIES,
    DEDUP_PREFIX,
    GLOBAL_PER_HOUR,
    URGENT_PASS_TTL_S,
    ChannelBuckets,
    ClockedLimiter,
    DedupStore,
    dedup_ttl,
    effective_limits,
)
from app.notifications.transports.base import NO_PUSH_DEVICE, Delivery, SendResult, Transport
from app.notifications.user_rules import (
    ADMIN_ONLY_EVENTS,
    INSTANCE_RULE_EVENTS,
    PushAudience,
    admin_audience,
    build_audience,
    instance_rule_audience,
    is_admin_owner,
    requester_audience,
)

logger = logging.getLogger(__name__)

QUEUE_MAX = 200
AGGREGATION_WINDOW_S = 60.0
STATE_PRUNE_S = 3600.0
FIRST_SEEN_PER_HOUR = 10
RETRY_WAITS = (0.0, 5.0, 30.0)
MAX_RETRY_AFTER_S = 120.0
LONG_RETRY_S = 300.0
LONG_RETRY_EVENTS = frozenset({"radio.offline", "telemetry.alert", quiet.DIGEST_EVENT})
# Ticks a digest the limits dropped is put back before it is given up (QUIET_TICK_S each).
QUIET_DIGEST_RETRIES = 10
COUNTER_FLUSH_S = 60.0
QUIET_TICK_S = 60.0
# The ``notification_event`` frames remembered to send each event once (MSHL-402).
FRAME_DEDUP_MAX = 256

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
class _Counts:
    """Counts of what a full bucket held back (never a text)."""

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

    def merged(self, other: _Counts | None) -> _Counts:
        if other is None:
            return self
        channels = dict(self.channels)
        for name, n in other.channels.items():
            channels[name] = channels.get(name, 0) + n
        return _Counts(
            self.count + other.count,
            self.dms + other.dms,
            self.private + other.private,
            self.other + other.other,
            channels,
        )

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
class _Held:
    """What a channel held back. On Web Push each held job also remembers whose devices
    wanted it (its ``PushAudience``, MSHL-361), so a summary reaches only the accounts that
    would have had at least one of the jobs, with their own counts: ``everyone`` counts the
    jobs without an audience, ``accounts`` those an account wants (key None: the accounts
    without an override of their own)."""

    count: int = 0
    everyone: _Counts = field(default_factory=_Counts)
    accounts: dict[int | None, _Counts] = field(default_factory=dict)
    uids: set[int] = field(default_factory=set)

    def add(self, job: _Job, *, per_account: bool = False) -> None:
        self.count += 1
        audience = job.delivery.audience if per_account else None
        if audience is None:
            self.everyone.add(job)
            return
        self.uids.update(audience.per_user)
        if audience.default is not None:
            self.accounts.setdefault(None, _Counts()).add(job)
        for uid, payload in audience.per_user.items():
            if payload is not None:
                self.accounts.setdefault(uid, _Counts()).add(job)

    def payload(self) -> dict[str, Any]:
        return self.everyone.payload()

    def audience(
        self,
        make: Callable[[_Counts], dict[str, Any]],
        paused: Collection[int] = (),
    ) -> PushAudience | None:
        """The summary each account gets (``make`` builds it from the account's counts);
        None when nobody would get one. ``paused`` are the accounts whose pause holds now."""

        def payload_for(key: int | None) -> dict[str, Any] | None:
            counts = self.everyone.merged(self.accounts.get(key))
            return make(counts) if counts.count else None

        known = (self.uids | {k for k in self.accounts if k is not None}) | set(paused)
        audience = PushAudience(
            default=payload_for(None),
            per_user={uid: None if uid in paused else payload_for(uid) for uid in known},
        )
        return None if audience.empty else audience


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
    first_seen_audience: _Held = field(default_factory=_Held)
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
        self._quiet_task: asyncio.Task[None] | None = None
        self._digest_retries: dict[tuple[str, str], int] = {}
        # Channels whose digest-mode window is known to be open (``digest_due:`` row exists).
        self._digest_open: set[str] = set()
        # ``(event, dedup_key)`` of the last frames sent: one frame per event (MSHL-402).
        self._frames_sent: OrderedDict[tuple[str, str], None] = OrderedDict()
        from app.notifications.heartbeat import HeartbeatManager

        self.heartbeats = HeartbeatManager(self)

    def start(self) -> None:
        """Start the quiet-hours digest loop and the channels' heartbeats (idempotent). The
        app calls it at startup so a digest owed after a restart is not waiting for the first
        event."""
        from app.background_tasks import spawn

        self._start_quiet_loop()
        spawn(self.heartbeats.start(), name="notify-heartbeat-start")

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
        per_account: bool = False,
        push_mention: tuple[NotificationEvent, Mapping[str, Any]] | None = None,
        push_already: PushAudience | None = None,
    ) -> int:
        """Queue ``event`` for each channel the rules give it (minus ``exclude``, the
        channels a producer rules out, such as a blocked sender's message), or for
        ``targets`` when the caller already decided. A producer that already loaded the
        rules or knows whether the conversation is a Public or ``#`` channel passes them
        (``rules``, ``is_open``) to spare the lookups, and tells whether the conversation is
        muted or the sender blocked (``muted``, ``blocked``: ``rules.event_channel_ids``).

        Web Push is decided per account (``user_rules``): without ``targets`` the admin rules and
        each account's own choices give its audience, and ``builtin:push`` is queued when anyone
        wants the event. A producer that routes only Web Push with ``targets`` asks for the same
        with ``per_account``; ``push_mention`` is the ``message.mention`` version of a channel
        message that names the radio, which the accounts that want mentions get instead of
        ``event``. ``push_already`` is the Web Push audience of the message a keyword alert
        comes from (MSHL-400): an administrator it already reaches gets no second push.
        Returns how many channels it queued
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
                per_account=per_account,
                push_mention=push_mention,
                push_already=push_already,
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
        per_account: bool = False,
        push_mention: tuple[NotificationEvent, Mapping[str, Any]] | None = None,
        push_already: PushAudience | None = None,
    ) -> int:
        if payload is None:
            if event is None:
                return 0
            payload = event.to_payload()
        if event is None:
            event = event_from_payload(payload)
        self.start()
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
        audience: PushAudience | None = None
        if (
            event is not None
            and (targets is None or per_account)
            and BUILTIN_PUSH_ID not in exclude
        ):
            channel_ids, audience = await self._push_per_account(
                channel_ids,
                rules,
                event,
                payload,
                push_mention,
                is_open=bool(is_open),
                muted=muted,
                blocked=blocked,
            )
        if (
            event is not None
            and event.event in ADMIN_ONLY_EVENTS
            and BUILTIN_PUSH_ID in channel_ids
        ):
            # An operational notice reaches administrators' devices only (MSHL-399).
            audience = await self._admin_push_audience(payload)
            if audience is None:
                channel_ids = [c for c in channel_ids if c != BUILTIN_PUSH_ID]
        elif (
            event is not None
            and event.event in INSTANCE_RULE_EVENTS
            and BUILTIN_PUSH_ID in channel_ids
        ):
            # A keyword rule or a watched node is the admin's instance setting: its Web Push
            # reaches administrators' devices only, fail closed (MSHL-400).
            audience = await self._instance_rule_push_audience(event, payload, push_already)
            if audience is None:
                channel_ids = [c for c in channel_ids if c != BUILTIN_PUSH_ID]
        channel_ids = [c for c in channel_ids if c not in exclude]
        channels = await self._channels(channel_ids, allow_legacy=targets is not None)
        if event is not None and any(c.enabled for c in channels):
            # Matched at least one rule: the Feed's frame, before pauses and quiet hours. A
            # channel message that names the radio is a mention whichever channels got which
            # version (MSHL-402): the frame carries the ``message.mention`` one.
            frame_event, frame_payload = push_mention or (event, payload)
            self._publish_frame(frame_event, frame_payload, is_open=bool(is_open), rules=rules)
        decided = rules_mod.apply_pauses(
            rules,
            [c.id for c in channels if c.enabled],
            name=name,
            urgent=urgent,
            channel_paused_until={c.id: c.paused_until for c in channels},
            now=self._wall(),
        )
        delivery = Delivery(name=name, payload=dict(payload), event=event, audience=audience)
        dedup_key = event.dedup_key if event is not None else ""
        by_id = {c.id: c for c in channels}
        queued = 0
        # Quiet hours come after the rules and every pause: an event nobody wanted (an
        # account's own choice, a mute) or that a pause holds is never counted here.
        subject_to_holds = not urgent and not rules_mod.always_deliver(rules, name)
        now = self._wall()
        is_quiet = subject_to_holds and quiet.is_quiet(rules, now)
        global_pause = subject_to_holds and rules_mod.globally_paused(rules, now)
        for target in decided:
            if target.paused:
                self._journal(target.channel_id, name, "paused", dedup_key=dedup_key)
                # What the global pause held is counted for the summary sent at resume (a
                # channel's own pause just drops it).
                if global_pause and await self._first_hold(
                    target.channel_id, name, dedup_key, scope="pause"
                ):
                    await self._count_held(
                        by_id[target.channel_id], delivery, rules, quiet.PAUSE_PREFIX
                    )
                continue
            if is_quiet and not by_id[target.channel_id].quiet_hours_exempt:
                # Dedup first: a re-emit of the same event is one event, counted once.
                if await self._first_hold(target.channel_id, name, dedup_key):
                    await self._suppress_quiet(by_id[target.channel_id], delivery, rules)
                continue
            if (
                subject_to_holds
                and by_id[target.channel_id].digest_interval_s > 0
                and quiet.digestible(name)
            ):
                # Digest mode: counted now, sent with the next summary. Same dedup as a quiet
                # hold: a re-emitted event is one event.
                if await self._first_hold(target.channel_id, name, dedup_key):
                    await self._hold_digest(by_id[target.channel_id], delivery, now)
                continue
            job = _Job(delivery, urgent=urgent, dedup_key=dedup_key, is_open=bool(is_open))
            if await self._admit(by_id[target.channel_id], job, rules):
                queued += 1
        return queued

    def _publish_frame(
        self,
        event: NotificationEvent,
        payload: Mapping[str, Any],
        *,
        is_open: bool,
        rules: Mapping[str, Any],
    ) -> None:
        """The ``notification_event`` frame, once per ``(event, dedup_key)`` among the last
        ``FRAME_DEDUP_MAX`` sent: a mention split across channels (a ``targets`` emit and the
        main one) or an event emitted again gives one frame."""
        key = (event.event, event.dedup_key)
        if key in self._frames_sent:
            return
        if ws_frame.publish(event, payload, is_open=is_open, rules=rules):
            self._frames_sent[key] = None
            while len(self._frames_sent) > FRAME_DEDUP_MAX:
                self._frames_sent.popitem(last=False)

    async def _push_per_account(
        self,
        channel_ids: list[str],
        rules: Mapping[str, Any],
        event: NotificationEvent,
        payload: Mapping[str, Any],
        push_mention: tuple[NotificationEvent, Mapping[str, Any]] | None,
        *,
        is_open: bool,
        muted: bool,
        blocked: bool,
    ) -> tuple[list[str], PushAudience | None]:
        """Let each account's rules decide Web Push (``user_rules``, MSHL-321).

        Returns the channel ids with ``builtin:push`` present when someone wants the event and
        absent otherwise, and the audience to carry (None while nobody has an override and no
        mention version exists: every device then gets ``payload``, as before)."""
        from app.repository.user_notification_rules import UserNotificationRulesRepository

        try:
            overrides = await UserNotificationRulesRepository.all_overrides()
        except Exception:
            logger.debug("account notification rules unreadable", exc_info=True)
            overrides = {}
        audience = build_audience(
            rules,
            overrides,
            event,
            payload,
            mention=push_mention,
            is_public_or_hashtag=is_open,
            muted=muted,
            blocked=blocked,
            now=self._wall(),
        )
        listed = BUILTIN_PUSH_ID in channel_ids
        if audience.empty:
            return [c for c in channel_ids if c != BUILTIN_PUSH_ID], None
        ids = channel_ids if listed else [*channel_ids, BUILTIN_PUSH_ID]
        return ids, (audience if overrides or push_mention is not None else None)

    async def _admin_owner_ids(self) -> list[int] | None:
        """The administrators who own a deliverable device; None when the owners cannot be
        read (the callers then send nothing: fail closed)."""
        from app.repository.push_subscriptions import PushSubscriptionRepository

        try:
            subs = await PushSubscriptionRepository.deliverable()
        except Exception:
            logger.debug("push owners unreadable, admin push not sent", exc_info=True)
            return None
        return list(
            dict.fromkeys(
                int(sub["user_id"]) for sub in subs if is_admin_owner(sub) and sub.get("user_id")
            )
        )

    async def _instance_rule_push_audience(
        self,
        event: NotificationEvent,
        payload: Mapping[str, Any],
        already: PushAudience | None,
    ) -> PushAudience | None:
        """The Web Push audience of a keyword alert or a watched-node sighting
        (``user_rules.instance_rule_audience``): administrators only, None (no Web Push) when
        there is none or the owners cannot be read."""
        from app.repository.user_notification_rules import UserNotificationRulesRepository

        admins = await self._admin_owner_ids()
        if not admins:
            return None
        try:
            overrides = await UserNotificationRulesRepository.all_overrides()
        except Exception:
            logger.debug("account notification rules unreadable", exc_info=True)
            overrides = {}
        return instance_rule_audience(
            admins, overrides, event, payload, already=already, now=self._wall()
        )

    async def _admin_push_audience(self, payload: Mapping[str, Any]) -> PushAudience | None:
        """The Web Push audience of an ``ADMIN_ONLY_EVENTS`` notice: the administrators who own
        a deliverable device. None (no Web Push) when there is none or the owners cannot be
        read: fail closed, a non-admin device never gets the notice."""
        admins = await self._admin_owner_ids()
        return admin_audience(admins, payload) if admins is not None else None

    async def claim_cooldown(self, *claims: tuple[str, int]) -> bool:
        """Start the cooldowns ``(key, seconds)`` unless one is still running: all or nothing,
        kept in the dedup store (``notification_state``, so a restart forgets nothing). Used by
        the keyword rules (per rule, and per rule and sender)."""
        await self._dedup.load()
        keys = [(f"{DEDUP_PREFIX}cooldown:{key}", ttl) for key, ttl in claims if ttl > 0]
        if any(self._dedup.seen(key) for key, _ in keys):
            return False
        for key, ttl in keys:
            self._dedup.mark(key, ttl)
        if keys:
            self._start_counter_loop()
        return True

    def forget_cooldowns(self, keys: Iterable[str]) -> None:
        """Drop cooldowns started with :meth:`claim_cooldown` (a watched node that was removed)."""
        for key in keys:
            self._dedup.discard(f"{DEDUP_PREFIX}cooldown:{key}")

    # -- quiet hours ------------------------------------------------------------

    async def _first_hold(
        self, channel_id: str, name: str, dedup_key: str, *, scope: str = ""
    ) -> bool:
        """Dedup for an event a quiet window or the global pause holds (``_admit``'s TTLs):
        False when this key was already seen on the channel, else mark it and say True, so a
        re-emitted event is counted once in the digest. A quiet-hours hold shares the keys of
        ``_admit`` (the event is in the summary: a copy after the window is no new message); the
        pause counts under its own ``scope`` and leaves the keys alone, because a paused event is
        not delivered and a copy after the resume still is."""
        ttl = dedup_ttl(name) if dedup_key else None
        if ttl is None:
            return True
        await self._dedup.load()
        key = f"{DEDUP_PREFIX}{scope + ':' if scope else ''}{channel_id}:{dedup_key}"
        if self._dedup.seen(key):
            return False
        self._dedup.mark(key, ttl)
        self._start_counter_loop()
        return True

    async def _suppress_quiet(
        self, channel: Channel, delivery: Delivery, rules: Mapping[str, Any]
    ) -> None:
        """Record a job the quiet hours hold: a journal row, and (``digest_at_end``) one counter
        UPDATE per (channel, event, conversation), or per wanted account on Web Push."""
        event = delivery.event
        self._journal(
            channel.id,
            delivery.name,
            "suppressed_quiet",
            dedup_key=event.dedup_key if event is not None else "",
        )
        await self._count_held(channel, delivery, rules, quiet.COUNTER_PREFIX)

    async def _count_held(
        self,
        channel: Channel,
        delivery: Delivery,
        rules: Mapping[str, Any],
        prefix: str,
        *,
        always: bool = False,
    ) -> None:
        """Count a held job for the digest of ``prefix`` (quiet hours, pause or digest mode);
        nothing when ``digest_at_end`` is off, except for the channel's own digest mode
        (``always``)."""
        from app.notifications.user_rules import conversation_state_key
        from app.repository.notification_channels import NotificationJournalRepository

        event = delivery.event
        if not always and not quiet.quiet_hours_of(rules)["digest_at_end"]:
            return
        conversation = (conversation_state_key(event) if event is not None else None) or ""
        audience = delivery.audience if channel.id == BUILTIN_PUSH_ID else None
        if audience is None:
            keys = [channel.id]
        else:
            keys = []
            if audience.default is not None:
                keys.append(quiet.account_channel_key(channel.id, None))
            keys += [
                quiet.account_channel_key(channel.id, uid)
                for uid, payload in audience.per_user.items()
                if payload is not None
            ]
        try:
            for key in keys:
                await NotificationJournalRepository.counter_incr(
                    quiet.counter_key(key, delivery.name, conversation, prefix)
                )
        except Exception:
            logger.debug("held-event counter not written", exc_info=True)

    # -- digest mode (MSHL-136) -------------------------------------------------

    async def _hold_digest(self, channel: Channel, delivery: Delivery, now: float) -> None:
        """Count a digestible event of a channel in digest mode. The first one of a window opens
        it: ``digest_due:<channel>`` holds the epoch of the summary (``digest_interval_s`` later),
        written before the counter so a counter never exists without its due row. The WebSocket
        frame already went out for this event (``_emit``), whatever the channel does with it."""
        from app.repository.notification_channels import NotificationJournalRepository

        try:
            if channel.id not in self._digest_open:
                key = quiet.DIGEST_DUE_PREFIX + channel.id
                if await NotificationJournalRepository.state_get(key, 0) is None:
                    due = int(now) + channel.digest_interval_s
                    await NotificationJournalRepository.state_set(key, str(due))
                self._digest_open.add(channel.id)
            self._journal(
                channel.id,
                delivery.name,
                "aggregated",
                dedup_key=delivery.event.dedup_key if delivery.event is not None else "",
            )
        except Exception:
            logger.debug("digest window not opened", exc_info=True)
            return
        await self._count_held(channel, delivery, {}, quiet.DIGEST_PREFIX, always=True)

    async def digest_tick(self) -> int:
        """Send the summaries of the channels in digest mode that are due, and clear their
        counters. Run by the minute loop, so a summary is up to ``QUIET_TICK_S`` late.

        Restart safety: counters and due rows are in ``notification_state``, so a restart keeps
        the window; one that ended while the app was down is sent at the first tick after the
        restart. A channel switched off (``digest_interval_s`` 0) is flushed at once rather than
        left holding counters; a disabled or deleted one drops them. Held while the global pause
        is on, while the channel's own pause lasts, and (but for a ``quiet_hours_exempt``
        channel) while quiet hours are on. Returns the summaries queued."""
        from app.repository.notification_channels import NotificationJournalRepository

        repo = NotificationJournalRepository
        counters = await repo.state_load_values(quiet.DIGEST_PREFIX)
        dues = await repo.state_load_values(quiet.DIGEST_DUE_PREFIX)
        if not counters and not dues:
            self._digest_open.clear()
            return 0
        rules = await rules_mod.load_rules()
        now = self._wall()
        if rules_mod.globally_paused(rules, now):
            return 0
        waiting: set[str] = set()
        for key in counters:
            parsed = quiet.parse_counter_key(key, quiet.DIGEST_PREFIX)
            if parsed is not None:
                waiting.add(parsed[0])
        due_ids = {key[len(quiet.DIGEST_DUE_PREFIX) :]: value for key, value in dues.items()}
        channels = {
            c.id: c for c in await self._channels(sorted(waiting | set(due_ids)), allow_legacy=True)
        }
        is_quiet = quiet.is_quiet(rules, now)
        queued = 0
        for channel_id in sorted(waiting | set(due_ids)):
            channel = channels.get(channel_id)
            try:
                due = float(due_ids[channel_id])
            except (KeyError, ValueError):
                due = None
            if channel is None or not channel.enabled:
                await self._digest_drop(channel_id)
                continue
            if due is None and channel.digest_interval_s > 0:
                # Counters without a due row (a crash, a race): open the window now.
                await repo.state_set(
                    quiet.DIGEST_DUE_PREFIX + channel_id, str(int(now) + channel.digest_interval_s)
                )
                self._digest_open.add(channel_id)
                continue
            if channel.digest_interval_s > 0 and due is not None and due > now:
                if due > now + channel.digest_interval_s:
                    # The interval was shortened while a window was open: it ends one new
                    # interval from now, not up to 24 h away.
                    await repo.state_set(
                        quiet.DIGEST_DUE_PREFIX + channel_id,
                        str(int(now) + channel.digest_interval_s),
                    )
                continue
            if is_quiet and not channel.quiet_hours_exempt:
                continue  # sent once the window is closed
            if channel.paused_until is not None and channel.paused_until > now:
                continue  # the channel's own pause is waited for
            queued += await self._digest_flush(channel, rules, now)
        return queued

    async def _digest_drop(self, channel_id: str) -> None:
        from app.repository.notification_channels import NotificationJournalRepository

        self._digest_open.discard(channel_id)
        self._digest_retries.pop((quiet.DIGEST_PREFIX, channel_id), None)
        await NotificationJournalRepository.state_delete(quiet.DIGEST_DUE_PREFIX + channel_id)
        for prefix in (
            f"{quiet.DIGEST_PREFIX}{channel_id}:",
            f"{quiet.DIGEST_PREFIX}{channel_id}@",
        ):
            await NotificationJournalRepository.counters_take(prefix)

    async def _digest_flush(self, channel: Channel, rules: Mapping[str, Any], now: float) -> int:
        """Close the window of ``channel``: take its counters and queue one summary. A summary the
        limits refuse is put back (the window reopens at once) for at most
        ``QUIET_DIGEST_RETRIES`` ticks, like the quiet-hours digest."""
        from app.repository.notification_channels import NotificationJournalRepository

        repo = NotificationJournalRepository
        # Due row first, then the counters: an event arriving in between opens a new window
        # instead of leaving a counter without a due row.
        self._digest_open.discard(channel.id)
        await repo.state_delete(quiet.DIGEST_DUE_PREFIX + channel.id)
        taken: dict[str, int] = {}
        for prefix in (
            f"{quiet.DIGEST_PREFIX}{channel.id}:",
            f"{quiet.DIGEST_PREFIX}{channel.id}@",
        ):
            taken.update(await repo.counters_take(prefix))
        accounts: dict[int | str | None, list[quiet.QuietRow]] = {}
        for key, count in taken.items():
            parsed = quiet.parse_counter_key(key, quiet.DIGEST_PREFIX)
            if parsed is not None:
                _channel_id, account, event, conversation = parsed
                accounts.setdefault(account, []).append(quiet.QuietRow(event, conversation, count))
        labels = await quiet.resolve_labels(
            r.conversation for rows in accounts.values() for r in rows
        )
        delivery = await self._digest(channel, accounts, labels, "digest")
        retry_key = (quiet.DIGEST_PREFIX, channel.id)
        if delivery is None:
            self._digest_retries.pop(retry_key, None)
            return 0
        if await self._admit(channel, _Job(delivery), rules):
            self._digest_retries.pop(retry_key, None)
            return 1
        if await self._put_back(quiet.DIGEST_PREFIX, channel.id, taken, count_retry=True):
            await repo.state_set(quiet.DIGEST_DUE_PREFIX + channel.id, str(int(now)))
            self._digest_open.add(channel.id)
        return 0

    def _start_quiet_loop(self) -> None:
        if self._quiet_task is None or self._quiet_task.done():
            from app.background_tasks import spawn

            self._quiet_task = spawn(self._quiet_loop(), name="notify-quiet-digest")

    async def _quiet_loop(self) -> None:
        while True:
            await asyncio.sleep(QUIET_TICK_S)
            try:
                await self.quiet_tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("quiet hours digest failed")
            try:
                await self.digest_tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("channel digest failed")
            try:
                await self.probe_tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("paused channel probe failed")
            # A safety net: the API reconciles the heartbeats after every channel change.
            await self.heartbeats.reconcile()

    async def quiet_tick(self) -> int:
        """Send the digests that are due and clear their counters: the pause summary once the
        global pause is over, the quiet-hours one once the window is closed. Both are held (the
        counters stay) while the global pause is on. Returns the digests queued."""
        rules = await rules_mod.load_rules()
        now = self._wall()
        if rules_mod.globally_paused(rules, now):
            return 0
        queued = await self._digest_tick(rules, now, quiet.PAUSE_PREFIX, "pause")
        if not quiet.is_quiet(rules, now):
            queued += await self._digest_tick(rules, now, quiet.COUNTER_PREFIX, "quiet_hours")
        return queued

    async def _digest_tick(
        self, rules: Mapping[str, Any], now: float, prefix: str, scope: str
    ) -> int:
        from app.repository.notification_channels import NotificationJournalRepository

        taken = await NotificationJournalRepository.counters_take(prefix)
        if not taken or not quiet.quiet_hours_of(rules)["digest_at_end"]:
            self._forget_retries(prefix, ())
            return 0
        by_channel: dict[str, dict[int | str | None, list[quiet.QuietRow]]] = {}
        for key, count in taken.items():
            parsed = quiet.parse_counter_key(key, prefix)
            if parsed is not None:
                channel_id, account, event, conversation = parsed
                rows = by_channel.setdefault(channel_id, {}).setdefault(account, [])
                rows.append(quiet.QuietRow(event, conversation, count))
        labels = await quiet.resolve_labels(
            row.conversation
            for accounts in by_channel.values()
            for r in accounts.values()
            for row in r
        )
        channels = await self._channels(list(by_channel), allow_legacy=True)
        queued = 0
        pending: set[str] = set()  # channels whose counters are still waiting for a retry
        for channel in channels:
            if not channel.enabled:
                continue  # a disabled channel gets no digest: its counters go
            if channel.paused_until is None or channel.paused_until <= now:
                delivery = await self._digest(channel, by_channel[channel.id], labels, scope)
                if delivery is None:
                    continue
                if await self._admit(channel, _Job(delivery), rules):
                    queued += 1
                    continue
                if await self._put_back(prefix, channel.id, taken, count_retry=True):
                    pending.add(channel.id)
                continue
            # The channel's own pause: wait for it without spending the retry budget, which
            # only counts real refusals (MSHL-374).
            await self._put_back(prefix, channel.id, taken, count_retry=False)
            pending.add(channel.id)
        self._forget_retries(prefix, pending)
        return queued

    def _forget_retries(self, prefix: str, keep: Iterable[str]) -> None:
        """Drop the retry counters of the channels that have no counters waiting any more (the
        digest went out, was given up, or the channel is gone), so a later digest starts with the
        full budget and the dict stays bounded by the channels with counters."""
        keep_set = set(keep)
        for key in [k for k in self._digest_retries if k[0] == prefix and k[1] not in keep_set]:
            del self._digest_retries[key]

    async def _put_back(
        self, prefix: str, channel_id: str, taken: Mapping[str, int], *, count_retry: bool
    ) -> bool:
        """Keep the counters for the next tick. ``count_retry`` is true when the digest was
        refused (limits): at most ``QUIET_DIGEST_RETRIES`` times in a row, then given up. A
        channel in its own pause is waited for with no limit (counters are bounded by their
        keys). Returns whether the counters were kept."""
        from app.repository.notification_channels import NotificationJournalRepository

        retry_key = (prefix, channel_id)
        if count_retry:
            attempts = self._digest_retries.get(retry_key, 0) + 1
            if attempts > QUIET_DIGEST_RETRIES:
                self._digest_retries.pop(retry_key, None)
                logger.warning("digest of %s given up after %d ticks", channel_id, attempts - 1)
                return False
            self._digest_retries[retry_key] = attempts
        try:
            for key, count in taken.items():
                parsed = quiet.parse_counter_key(key, prefix)
                if parsed is not None and parsed[0] == channel_id:
                    await NotificationJournalRepository.counter_add(key, count)
        except Exception:
            logger.debug("digest counters not put back", exc_info=True)
        return True

    async def _digest(
        self,
        channel: Channel,
        accounts: dict[int | str | None, list[quiet.QuietRow]],
        labels: Mapping[str, quiet.Label],
        scope: str = "quiet_hours",
    ) -> Delivery | None:
        names = channel.include_message_text == "always"
        shared = accounts.get(None, [])
        if channel.id != BUILTIN_PUSH_ID or set(accounts) <= {None}:
            payload = quiet.build_digest(shared, labels, names=names, scope=scope)
            return Delivery(quiet.DIGEST_EVENT, payload) if payload else None
        from app.notifications.user_rules import PushAudience, is_paused
        from app.repository.user_notification_rules import UserNotificationRulesRepository

        try:
            overrides = await UserNotificationRulesRepository.all_overrides()
        except Exception:
            logger.debug("account notification rules unreadable", exc_info=True)
            overrides = {}
        now = self._wall()
        known = {a for a in accounts if isinstance(a, int)} | {
            uid for uid, ov in overrides.items() if is_paused(ov, now)
        }

        def digest_for(account: int | str | None) -> dict[str, Any] | None:
            rows = [*shared, *accounts.get(account, [])]
            return quiet.build_digest(rows, labels, names=names, scope=scope)

        per_user = {
            uid: None if is_paused(overrides.get(uid, {}), now) else digest_for(uid)
            for uid in known
        }
        audience = PushAudience(default=digest_for(quiet.ACCOUNT_DEFAULT), per_user=per_user)
        if audience.empty:
            return None
        payload = audience.default or next(p for p in per_user.values() if p is not None)
        return Delivery(quiet.DIGEST_EVENT, dict(payload), audience=audience)

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
        # The only real suspension point (a counter read on the first call of the UTC day)
        # comes first, so the cap test below and its charge never have a wait between them.
        buckets = await self._channel_buckets(worker, channel)
        cap = self._global_cap(rules)
        if not cap.has_capacity():
            self._update_capped()
            self._journal(channel_id, name, "rate_limited", dedup_key=job.dedup_key)
            return False
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
        if not cap.try_acquire():  # test and take in one step: never waits
            self._update_capped()
            self._journal(channel_id, name, "rate_limited", dedup_key=job.dedup_key)
            return False
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
        worker.held.add(job, per_account=worker.channel_id == BUILTIN_PUSH_ID)
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
        summary = Delivery(GROUPED_EVENT, held.payload())
        if worker.channel_id == BUILTIN_PUSH_ID:
            summary = await self._push_summary(held, _Counts.payload, GROUPED_EVENT)
            if summary is None:  # no account would have had any of the held jobs
                return
        if buckets is not None and buckets.quota_exhausted():
            self._journal(worker.channel_id, GROUPED_EVENT, "quota_exceeded")
            return
        # A summary counts against the global cap like any send: the wait above ended with
        # room, and nothing suspends between that test and this charge.
        if self._cap is not None and not self._cap.try_acquire():
            self._update_capped()
            self._journal(worker.channel_id, GROUPED_EVENT, "rate_limited")
            return
        if buckets is not None:
            await buckets.take()
        self._update_capped()
        self._enqueue(worker.channel_id, _Job(summary))

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
        worker.first_seen_audience.add(job, per_account=worker.channel_id == BUILTIN_PUSH_ID)
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
                held, worker.first_seen_audience = worker.first_seen_audience, _Held()
        except asyncio.CancelledError:
            if worker.first_seen_held:
                self._journal(worker.channel_id, FIRST_SEEN_GROUPED_EVENT, "aborted")
                await self._flush_journal()
            raise
        summary = _grouped(FIRST_SEEN_GROUPED_EVENT, count)
        if worker.channel_id == BUILTIN_PUSH_ID:
            pushed = await self._push_summary(
                held,
                lambda counts: _grouped(FIRST_SEEN_GROUPED_EVENT, counts.count).payload,
                FIRST_SEEN_GROUPED_EVENT,
            )
            if pushed is None:
                return
            summary = pushed
        self._enqueue(worker.channel_id, _Job(summary))

    async def _push_summary(
        self,
        held: _Held,
        make: Callable[[_Counts], dict[str, Any]],
        name: str,
    ) -> Delivery | None:
        """The Web Push summary of what was held (MSHL-361): each account gets it only when
        its own rules wanted at least one held job, counted from those jobs alone, and an
        account paused now (MSHL-363) gets none. None when nobody is left."""
        from app.notifications.user_rules import is_paused
        from app.repository.user_notification_rules import UserNotificationRulesRepository

        try:
            overrides = await UserNotificationRulesRepository.all_overrides()
        except Exception:
            logger.debug("account notification rules unreadable", exc_info=True)
            overrides = {}
        now = self._wall()
        paused = {uid for uid, ov in overrides.items() if is_paused(ov, now)}
        audience = held.audience(make, paused)
        if audience is None:
            return None
        payload = audience.default or next(
            (p for p in audience.per_user.values() if p is not None), {"event": name, "count": 0}
        )
        return Delivery(name, dict(payload), audience=audience)

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

    def count_sent(self, channel_id: str) -> None:
        """Count one sent notification for ``channel_id`` (flushed by the counter loop)."""
        key = (channel_id, int(self._wall()) // 3600)
        self._sent[key] = self._sent.get(key, 0) + 1
        self._start_counter_loop()

    async def _succeeded(self, channel: Channel) -> None:
        now = int(self._wall())
        self.count_sent(channel.id)
        self._ok_at[channel.id] = now
        live = self._live.setdefault(channel.id, _Live())
        failing = (
            live.failure_count if live.failure_count is not None else channel.failure_count
        ) > 0
        live.last_ok_at = now
        if failing:
            live.failure_count, live.last_error = 0, None
            await self._write_status(channel.id, ok=True)
        if channel.paused_until is not None:
            channel = await self._unpause(channel)
        self._start_counter_loop()
        self._report(channel)

    async def _record_failure(
        self, channel: Channel, code: str | None, secrets: dict[str, str] | None = None
    ) -> None:
        from app.secret_fields import scrub_error

        error = scrub_error(code or "send_failed", (secrets or {}).values())
        live = self._live.setdefault(channel.id, _Live())
        base = live.failure_count if live.failure_count is not None else channel.failure_count
        count = base + 1
        live.failure_count, live.last_error = count, error
        await self._write_status(channel.id, ok=False, error=error)
        if count >= failures.PAUSE_AFTER:
            channel = await self._pause_if_running(channel)
        self._report(channel)
        if count == failures.FAILED_EVENT_AFTER:
            self._broadcast_health()  # notifications_failing turns on
        if count >= failures.FAILED_EVENT_AFTER:
            await failures.announce(
                channel.id,
                channel_id=channel.id,
                name=channel.name,
                error=error,
                exclude={channel.id},
                now=int(self._wall()),
                emit_fn=self.emit,
            )

    async def _pause_if_running(self, channel: Channel) -> Channel:
        """Pause ``channel`` for its next backoff step unless a pause already holds it."""
        from app.repository.notification_channels import NotificationChannelRepository

        try:
            fresh = await NotificationChannelRepository.get(channel.id)
        except Exception:
            logger.debug("channel not read for the auto-pause", exc_info=True)
            return channel
        if fresh is None:
            return channel  # a legacy medium without a row: nothing to pause
        now = int(self._wall())
        if fresh.paused_until is not None and fresh.paused_until > now:
            return channel.model_copy(update={"paused_until": fresh.paused_until})
        return await self._pause(channel, now)

    async def _pause(self, channel: Channel, now: int) -> Channel:
        from app.repository.notification_channels import (
            NotificationChannelRepository,
            NotificationJournalRepository,
        )

        key = failures.autopause_key(channel.id)
        try:
            previous = await NotificationJournalRepository.state_get(key)
            step = int(previous) + 1 if previous is not None else 0
        except (TypeError, ValueError):
            step = 0
        until = now + failures.backoff_s(step)
        await NotificationJournalRepository.state_set(key, str(step))
        await NotificationChannelRepository.set_paused_until(channel.id, until)
        logger.warning(
            "Notification channel %s paused for %d s after repeated failures",
            channel.id,
            until - now,
        )
        return channel.model_copy(update={"paused_until": until})

    async def _unpause(self, channel: Channel) -> Channel:
        from app.repository.notification_channels import (
            NotificationChannelRepository,
            NotificationJournalRepository,
        )

        try:
            await NotificationJournalRepository.state_delete(failures.autopause_key(channel.id))
            await NotificationChannelRepository.set_paused_until(channel.id, None)
        except Exception:
            logger.debug("channel pause not cleared", exc_info=True)
            return channel
        logger.info("Notification channel %s resumed", channel.id)
        return channel.model_copy(update={"paused_until": None})

    async def resume(self, channel_id: str) -> Channel | None:
        """The Resume button: clear the pause, the backoff step and the failures (the next
        failure counts from one). Returns the channel, ``None`` when it does not exist."""
        from app.repository.notification_channels import NotificationChannelRepository

        channel = await NotificationChannelRepository.get(channel_id)
        if channel is None:
            return None
        await self._unpause(channel)
        await NotificationChannelRepository.clear_failures(channel_id)
        live = self._live.setdefault(channel_id, _Live())
        live.failure_count, live.last_error = 0, None
        channel = await NotificationChannelRepository.get(channel_id) or channel
        self._report(channel)
        self._broadcast_health()
        return channel

    async def probe_tick(self) -> int:
        """Send a test to each auto-paused channel whose pause ran out: a success resumes
        it, a failure pauses it for the next backoff step. Returns the probes sent."""
        from app.repository.notification_channels import (
            NotificationChannelRepository,
            NotificationJournalRepository,
        )

        now = int(self._wall())
        sent = 0
        for channel in await NotificationChannelRepository.list_all():
            if not channel.enabled or channel.secret_unreadable:
                continue
            if channel.paused_until is None or channel.paused_until > now:
                continue
            key = failures.autopause_key(channel.id)
            if await NotificationJournalRepository.state_get(key) is None:
                continue  # not an auto-pause
            loaded = await NotificationChannelRepository.get_for_send(channel.id)
            if loaded is None:
                continue
            fresh, secrets = loaded
            result = await self.send_test(fresh, secrets, record=True)
            if result.status == "skipped":
                continue  # nothing sent: the channel stays paused until someone can be reached
            sent += 1
            if not result.ok:
                after = await NotificationChannelRepository.get(channel.id)
                if after is not None and (after.paused_until or 0) <= now:
                    self._report(await self._pause(after, now))  # a failure that did not count
        return sent

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
                await NotificationJournalRepository.state_trim_prefix(
                    DEDUP_PREFIX, DEDUP_MAX_ENTRIES
                )
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
        self,
        channel: Channel,
        secrets: dict[str, str],
        *,
        record: bool = False,
        requester_id: int | None = None,
    ) -> SendResult:
        """One ``notify.test`` send through the channel's transport (so through the
        egress guard), outside the queue: no retry, no journal row, no counter. With
        ``record`` (a saved channel) the answer also updates its status. Never raises.

        On Web Push (MSHL-407) the test reaches the devices of ``requester_id`` (the account
        that pressed **Test**) only; without one (the probe of an auto-paused channel) the
        administrators' devices, and nothing when they cannot be read (fail closed): the
        result is then ``skipped`` (``no_push_device``) and nothing is recorded, as when
        the audience has no device."""
        from app.notifications.events import TestData, TestEvent, dedup_hash

        transport = self._transport_for(channel)
        if transport is None:
            return SendResult("failed", error_code="no_transport")
        event = TestEvent(
            event="notify.test",
            dedup_key=dedup_hash("notify.test", time.time_ns()),
            data=TestData(),
        )
        payload = event.to_payload()
        audience: PushAudience | None = None
        if channel.type == "web_push":
            audience = (
                requester_audience(requester_id, payload)
                if requester_id is not None
                else await self._admin_push_audience(payload)
            )
        if channel.type == "web_push" and audience is None:
            # Nobody to reach: nothing is sent. Neither a success (it would resume an
            # untested channel) nor a failure, so pause, failures and status stay as is.
            return SendResult("skipped", error_code=NO_PUSH_DEVICE)
        delivery = Delivery(name=event.event, payload=payload, event=event, audience=audience)
        try:
            result = await transport.send(channel, None, delivery, secrets)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("notification test on %s raised %s", channel.type, type(exc).__name__)
            result = SendResult("failed", error_code="send_failed")
        if record and result.status != "skipped":
            if result.ok:
                live = self._live.setdefault(channel.id, _Live())
                failing = (
                    live.failure_count if live.failure_count is not None else channel.failure_count
                ) > 0
                live.last_ok_at = int(self._wall())
                if failing:
                    live.failure_count, live.last_error = 0, None
                    await self._write_status(channel.id, ok=True)
                if channel.paused_until is not None:
                    channel = await self._unpause(channel)
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
            if channel.secret_unreadable:
                # The secrets key cannot open a stored secret (MSHL-133): that is the reason.
                last_error = channel.last_error
            out[channel.id] = {
                "status": _status_of(channel, live, now),
                "last_error": last_error,
                "last_ok_at": live.last_ok_at or channel.last_ok_at,
            }
        return out

    async def failing(self) -> bool:
        """Whether a channel needs a look: ``FAILED_EVENT_AFTER`` failures or more, paused,
        or a stored secret the key cannot open (MSHL-133). The health ``notifications_failing``."""
        from app.repository.notification_channels import NotificationChannelRepository

        now = self._wall()
        for channel in await NotificationChannelRepository.list_all():
            if channel.secret_unreadable:
                return True
            if not channel.enabled:
                continue
            live = self._live.get(channel.id) or _Live()
            count = live.failure_count if live.failure_count is not None else channel.failure_count
            if count >= failures.FAILED_EVENT_AFTER or _status_of(channel, live, now) == "paused":
                return True
        return False

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
    per_account: bool = False,
    push_mention: tuple[NotificationEvent, Mapping[str, Any]] | None = None,
    push_already: PushAudience | None = None,
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
        per_account=per_account,
        push_mention=push_mention,
        push_already=push_already,
    )


def start_notifications() -> None:
    """Start the dispatcher's background loops at app startup (quiet-hours digest,
    heartbeats)."""
    notification_dispatcher().start()


def notifications_capped() -> bool:
    """The health payload's ``notifications_capped`` (never raises)."""
    try:
        return bool(_instance is not None and _instance.capped)
    except Exception:
        return False


async def notifications_failing() -> bool:
    """The health payload's ``notifications_failing`` (never raises)."""
    try:
        return await notification_dispatcher().failing()
    except Exception:
        logger.debug("notification failing flag unavailable", exc_info=True)
        return False


async def notification_statuses() -> dict[str, dict[str, Any]]:
    """The health payload's ``notification_statuses`` (never raises)."""
    try:
        return await notification_dispatcher().statuses()
    except Exception:
        logger.debug("notification statuses unavailable", exc_info=True)
        return {}
