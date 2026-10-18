"""The notification dispatcher (MSHL-109): queues and workers per channel, the minimal
limiter, the retry policy, the journal and counters, the channel status in health, the
shutdown and the legacy adapter.

Retries and spacing run on a fake clock (``Dispatcher(clock=..., sleep=...)``); the
isolation test uses the real notification executor and a transport that holds its thread.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import httpx
import pytest

from app.notifications import dispatcher as dispatcher_module
from app.notifications.channels import Channel, min_interval_s
from app.notifications.dispatcher import (
    GROUPED_EVENT,
    LONG_RETRY_S,
    MAX_RETRY_AFTER_S,
    Dispatcher,
    notification_dispatcher,
)
from app.notifications.events import event_from_payload
from app.notifications.executor import run_blocking
from app.notifications.rules import event_channel_ids, message_channel_ids
from app.notifications.transports.base import Delivery, SendResult, result_from_exception
from app.repository.notification_channels import (
    NotificationChannelRepository as Channels,
)
from app.repository.notification_channels import invalidate_cache

pytestmark = pytest.mark.async_notifications

DM_KEY = "ab" * 32
SECRET_TEXT = "meet at the old mill at 9"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


@dataclass
class StubTransport:
    """Answers ``script`` in order (the last answer repeats); records each call."""

    clock: FakeClock | None = None
    script: list[SendResult] = field(default_factory=lambda: [SendResult("ok")])
    calls: list[tuple[str, str, float]] = field(default_factory=list)
    payloads: list[dict] = field(default_factory=list)
    gate: asyncio.Event | None = None

    async def send(self, channel, rendered, event, secrets) -> SendResult:
        self.calls.append((channel.id, event.name, self.clock() if self.clock else 0.0))
        self.payloads.append(dict(event.payload))
        if self.gate is not None:
            await self.gate.wait()
        answer = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return answer


db = None  # the test database, set by ``_wired``


@pytest.fixture(autouse=True)
def _wired(request):
    global db
    invalidate_cache()
    db = request.getfixturevalue("test_db") if "test_db" in request.fixturenames else None
    yield
    invalidate_cache()


async def _add_channel(channel_id: str, type_: str, *, preset: str | None = None, **cols) -> None:
    async with db.tx() as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO notification_channels "
            "(id, type, preset, name, enabled, config, secrets, created_at, paused_until) "
            "VALUES (?, ?, ?, ?, ?, '{}', ?, 1, ?)",
            (
                channel_id,
                type_,
                preset,
                channel_id,
                cols.get("enabled", 1),
                json.dumps(cols.get("secrets", {})),
                cols.get("paused_until"),
            ),
        )
    invalidate_cache()


async def _set_rules(rules: dict) -> None:
    async with db.tx() as conn:
        await conn.execute(
            "UPDATE app_settings SET notification_rules = ? WHERE id = 1", (json.dumps(rules),)
        )


async def _deliveries() -> list[dict]:
    async with db.readonly() as conn:
        async with conn.execute("SELECT * FROM notification_deliveries ORDER BY id") as cursor:
            return [dict(row) for row in await cursor.fetchall()]


def _dispatcher(transport, clock: FakeClock | None = None, **kwargs) -> Dispatcher:
    if clock is None:
        return Dispatcher(transport_for=lambda _channel: transport, **kwargs)
    return Dispatcher(
        clock=clock, sleep=clock.sleep, transport_for=lambda _channel: transport, **kwargs
    )


def _dm(text: str = SECRET_TEXT, n: int = 0) -> dict:
    return {
        "event": "message",
        "type": "PRIV",
        "conversation_key": DM_KEY,
        "text": text,
        "sender_name": "Alice",
        "id": n,
    }


def _chan(n: int) -> dict:
    return {
        "event": "message",
        "type": "CHAN",
        "conversation_key": "8B3387E9C5CDEA6AC9E5EDBAA115CD72",
        "text": f"hello {n}",
        "sender_name": "Bob",
        "channel_name": "Public",
        "id": n,
    }


async def _settle(dispatcher: Dispatcher) -> None:
    """Until the workers, the long retries and the journal are done."""
    for _ in range(10):
        await dispatcher.wait_idle()
        pending = [t for w in dispatcher._workers.values() for t in w.long_retries]
        if not pending:
            break
        await asyncio.gather(*pending, return_exceptions=True)
    await dispatcher.wait_idle()


# -- retry policy (fake clock) -----------------------------------------------------


@pytest.mark.asyncio
async def test_retry_waits_are_0_5_30_then_success(test_db):
    await _add_channel("webhook-1", "webhook")
    clock = FakeClock()
    transport = StubTransport(
        clock, [SendResult("retry", error_code="service_error")] * 2 + [SendResult("ok")]
    )
    dispatcher = _dispatcher(transport, clock)
    await dispatcher.emit(None, payload={"event": "oss_update"}, targets=["webhook-1"])
    await _settle(dispatcher)
    assert [c[2] for c in transport.calls] == [1000.0, 1005.0, 1035.0]
    assert clock.sleeps == [5.0, 30.0]
    assert await _deliveries() == []  # a success writes no row


@pytest.mark.asyncio
async def test_no_retry_on_401_and_the_error_is_recorded(test_db):
    await _add_channel("webhook-1", "webhook")
    clock = FakeClock()
    transport = StubTransport(clock, [SendResult("failed", error_code="http_401")])
    dispatcher = _dispatcher(transport, clock)
    await dispatcher.emit(None, payload={"event": "oss_update"}, targets=["webhook-1"])
    await _settle(dispatcher)
    assert len(transport.calls) == 1
    assert clock.sleeps == []
    [row] = await _deliveries()
    assert (row["status"], row["error"], row["attempts"]) == ("failed", "http_401", 1)
    channel = await Channels.get("webhook-1")
    assert channel is not None
    assert (channel.failure_count, channel.last_error) == (1, "http_401")


def test_http_answers_map_to_the_policy():
    request = httpx.Request("POST", "https://hook.example/x")

    def answer(status: int, headers: dict | None = None) -> SendResult:
        response = httpx.Response(status, headers=headers or {}, request=request)
        return result_from_exception(httpx.HTTPStatusError("x", request=request, response=response))

    assert answer(401) == SendResult("failed", error_code="http_401")
    assert answer(404).status == "failed"
    assert answer(503) == SendResult("retry", error_code="service_error")
    assert answer(429, {"Retry-After": "7"}) == SendResult(
        "retry", retry_after=7.0, error_code="rate_limited"
    )
    assert result_from_exception(httpx.ReadTimeout("x")).status == "retry"
    assert result_from_exception(httpx.ConnectError("x")).status == "retry"


@pytest.mark.asyncio
async def test_retry_after_is_honoured_and_capped(test_db):
    await _add_channel("webhook-1", "webhook")
    clock = FakeClock()
    transport = StubTransport(
        clock,
        [
            SendResult("retry", retry_after=12.0, error_code="rate_limited"),
            SendResult("retry", retry_after=10_000.0, error_code="rate_limited"),
            SendResult("ok"),
        ],
    )
    dispatcher = _dispatcher(transport, clock)
    await dispatcher.emit(None, payload={"event": "oss_update"}, targets=["webhook-1"])
    await _settle(dispatcher)
    assert clock.sleeps == [12.0, MAX_RETRY_AFTER_S]
    assert len(transport.calls) == 3


@pytest.mark.asyncio
async def test_a_failing_message_is_dropped_as_stale(test_db):
    await _add_channel("webhook-1", "webhook")
    clock = FakeClock()
    transport = StubTransport(clock, [SendResult("retry", error_code="timeout")])
    dispatcher = _dispatcher(transport, clock)
    await dispatcher.emit(None, payload=_dm(), targets=["webhook-1"])
    await _settle(dispatcher)
    assert len(transport.calls) == 3
    assert LONG_RETRY_S not in clock.sleeps
    [row] = await _deliveries()
    assert (row["event"], row["status"], row["attempts"]) == ("message.dm", "failed", 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"event": "radio_offline"}, {"event": "telemetry_alert"}])
async def test_radio_offline_and_telemetry_get_one_long_retry(test_db, payload):
    await _add_channel("webhook-1", "webhook")
    clock = FakeClock()
    transport = StubTransport(
        clock, [SendResult("retry", error_code="timeout")] * 3 + [SendResult("ok")]
    )
    dispatcher = _dispatcher(transport, clock)
    await dispatcher.emit(None, payload=payload, targets=["webhook-1"])
    await _settle(dispatcher)
    assert clock.sleeps == [5.0, 30.0, LONG_RETRY_S]
    assert len(transport.calls) == 4
    assert await _deliveries() == []
    channel = await Channels.get("webhook-1")
    assert channel is not None and channel.failure_count == 0


@pytest.mark.asyncio
async def test_a_failed_long_retry_is_recorded_once(test_db):
    await _add_channel("webhook-1", "webhook")
    clock = FakeClock()
    transport = StubTransport(clock, [SendResult("retry", error_code="timeout")])
    dispatcher = _dispatcher(transport, clock)
    await dispatcher.emit(None, payload={"event": "radio_offline"}, targets=["webhook-1"])
    await _settle(dispatcher)
    assert len(transport.calls) == 4
    [row] = await _deliveries()
    assert (row["event"], row["status"], row["attempts"]) == ("radio.offline", "failed", 4)


@pytest.mark.asyncio
async def test_a_success_resets_failure_count(test_db):
    await _add_channel("webhook-1", "webhook")
    clock = FakeClock()
    transport = StubTransport(clock, [SendResult("failed", error_code="http_400")] * 2)
    transport.script.append(SendResult("ok"))
    dispatcher = _dispatcher(transport, clock)
    for _ in range(2):
        await dispatcher.emit(None, payload={"event": "oss_update"}, targets=["webhook-1"])
        await _settle(dispatcher)
    channel = await Channels.get("webhook-1")
    assert channel is not None and channel.failure_count == 2
    await dispatcher.emit(None, payload={"event": "oss_update"}, targets=["webhook-1"])
    await _settle(dispatcher)
    channel = await Channels.get("webhook-1")
    assert channel is not None
    assert (channel.failure_count, channel.last_error) == (0, None)


@pytest.mark.asyncio
async def test_a_raising_transport_is_a_failure_not_a_crash(test_db):
    await _add_channel("webhook-1", "webhook")

    class Boom:
        async def send(self, *args):
            raise RuntimeError("https://user:token@hook.example/secret")

    dispatcher = _dispatcher(Boom(), FakeClock())
    await dispatcher.emit(None, payload={"event": "oss_update"}, targets=["webhook-1"])
    await _settle(dispatcher)
    [row] = await _deliveries()
    assert row["error"] == "send_failed"


# -- isolation -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_slow_channel_never_delays_another_nor_web_push(test_db):
    await _add_channel("apprise-slow", "apprise")
    await _add_channel("webhook-fast", "webhook")
    done: dict[str, float] = {}
    started = time.monotonic()

    class Transports:
        async def send(self, channel, rendered, event, secrets):
            if channel.id == "apprise-slow":
                # A blocking send holding its thread in the real notification pool.
                await run_blocking(time.sleep, 1.5)
            else:
                await run_blocking(lambda: None)
            done[channel.id] = time.monotonic() - started
            return SendResult("ok")

    dispatcher = Dispatcher(transport_for=lambda _c: Transports())
    for _ in range(2):
        await dispatcher.emit(
            None,
            payload={"event": "oss_update"},
            targets=["apprise-slow", "webhook-fast", "builtin:push"],
        )
    for _ in range(100):
        if "webhook-fast" in done and "builtin:push" in done:
            break
        await asyncio.sleep(0.02)
    assert done["webhook-fast"] < 0.5
    assert done["builtin:push"] < 0.5
    assert "apprise-slow" not in done  # still holding its thread
    await dispatcher.wait_idle()


# -- limiter ----------------------------------------------------------------------


def test_minimum_intervals_come_from_the_registry():
    assert min_interval_s("apprise", "telegram") == 3.0
    assert min_interval_s("apprise", "discord") == 2.0
    assert min_interval_s("ntfy") == 1.0
    assert min_interval_s("email") == 10.0
    assert min_interval_s("webhook") == 0.0
    assert min_interval_s("web_push") == 0.0


@pytest.mark.asyncio
async def test_a_burst_on_a_telegram_channel_is_spaced_and_summarised(test_db):
    await _add_channel("apprise-tg", "apprise", preset="telegram")
    clock = FakeClock()
    gate = asyncio.Event()  # the first send waits until the whole burst is in
    transport = StubTransport(clock, gate=gate)
    dispatcher = _dispatcher(transport, clock)
    for n in range(50):
        await dispatcher.emit(None, payload=_chan(n), targets=["apprise-tg"])
    gate.set()
    await _settle(dispatcher)

    stamps = [c[2] for c in transport.calls]
    assert all(b - a == 3.0 for a, b in zip(stamps, stamps[1:], strict=False))
    grouped = [p for p in transport.payloads if p["event"] == GROUPED_EVENT]
    assert len(grouped) == 1
    singles = len(transport.payloads) - 1
    assert transport.payloads[-1] == grouped[0]
    assert singles + grouped[0]["count"] == 50
    assert singles == 21  # one in flight, MAX_PENDING queued
    rows = await _deliveries()
    assert [r["status"] for r in rows] == ["aggregated"] * grouped[0]["count"]


@pytest.mark.asyncio
async def test_a_full_queue_records_rate_limited(test_db):
    await _add_channel("webhook-1", "webhook")
    gate = asyncio.Event()
    transport = StubTransport(gate=gate)
    dispatcher = _dispatcher(transport, FakeClock(), queue_max=3)
    # Urgent jobs are never folded into a summary: only the queue bound stops them.
    for _ in range(6):
        await dispatcher.emit(None, payload={"event": "radio_offline"}, targets=["webhook-1"])
        await asyncio.sleep(0)
    gate.set()
    await _settle(dispatcher)
    rows = await _deliveries()
    limited = [r for r in rows if r["status"] == "rate_limited"]
    assert len(limited) == 6 - 3 - 1  # three queued, one in flight
    assert len(transport.calls) == 4


@pytest.mark.asyncio
async def test_first_seen_is_capped_then_grouped(test_db):
    await _add_channel("webhook-1", "webhook")
    transport = StubTransport()
    dispatcher = _dispatcher(
        transport, FakeClock(), first_seen_per_hour=10, first_seen_period_s=0.5
    )
    for n in range(15):
        await dispatcher.emit(
            None,
            payload={"event": "first_seen", "public_key": f"{n:064x}", "contact_type": 1},
            targets=["webhook-1"],
        )
    await dispatcher.wait_idle()
    worker = dispatcher._workers["webhook-1"]
    assert worker.first_seen_flusher is not None
    await worker.first_seen_flusher
    await dispatcher.wait_idle()
    assert len(transport.payloads) == 11
    assert transport.payloads[-1] == {"event": "first_seen_grouped", "count": 5}
    assert [r["status"] for r in await _deliveries()] == ["aggregated"] * 5


# -- journal, counters, text --------------------------------------------------------


@pytest.mark.asyncio
async def test_deliveries_never_hold_message_text_and_successes_are_counters(test_db):
    await _add_channel("webhook-bad", "webhook", secrets={"url": "https://h.example/t0ken"})
    await _add_channel("webhook-ok", "webhook")

    class Mixed:
        async def send(self, channel, rendered, event, secrets):
            if channel.id == "webhook-bad":
                return SendResult("failed", error_code="http_403 https://h.example/t0ken")
            return SendResult("ok")

    dispatcher = _dispatcher(Mixed(), FakeClock())
    for n in range(3):
        await dispatcher.emit(None, payload=_dm(n=n), targets=["webhook-bad", "webhook-ok"])
    await _settle(dispatcher)
    await dispatcher.flush_counters()

    async with db.readonly() as conn:
        dump = []
        for table in ("notification_deliveries", "notification_counters", "notification_channels"):
            async with conn.execute(f"SELECT * FROM {table}") as cursor:  # noqa: S608
                dump += [dict(row) for row in await cursor.fetchall()]
    text = json.dumps(dump)
    assert SECRET_TEXT not in text
    assert "Alice" not in text
    assert "t0ken" not in json.dumps([r.get("last_error") for r in dump])
    rows = await _deliveries()
    assert {r["channel_id"] for r in rows} == {"webhook-bad"}
    assert all(r["status"] == "failed" and "t0ken" not in r["error"] for r in rows)
    async with db.readonly() as conn:
        async with conn.execute(
            "SELECT channel_id, SUM(sent) FROM notification_counters GROUP BY channel_id"
        ) as cursor:
            assert [tuple(r) for r in await cursor.fetchall()] == [("webhook-ok", 3)]
    ok = await Channels.get("webhook-ok")
    assert ok is not None and ok.last_ok_at is not None


@pytest.mark.asyncio
async def test_global_pause_holds_all_but_urgent_and_always_deliver(test_db):
    await _add_channel("webhook-1", "webhook")
    await _set_rules(
        {
            "version": 1,
            "paused_until": int(time.time()) + 3600,
            "events": {"app.update": {"channels": ["webhook-1"], "always_deliver": False}},
        }
    )
    transport = StubTransport()
    dispatcher = _dispatcher(transport, FakeClock())
    await dispatcher.emit(None, payload={"event": "oss_update"}, targets=["webhook-1"])
    await dispatcher.emit(None, payload={"event": "radio_offline"}, targets=["webhook-1"])
    await _settle(dispatcher)
    assert [c[1] for c in transport.calls] == ["radio.offline"]
    [row] = await _deliveries()
    assert (row["event"], row["status"]) == ("app.update", "paused")


@pytest.mark.asyncio
async def test_disabled_and_unknown_channels_are_skipped(test_db):
    await _add_channel("webhook-off", "webhook", enabled=0)
    transport = StubTransport()
    dispatcher = _dispatcher(transport, FakeClock())
    await dispatcher.emit(None, payload={"event": "oss_update"}, targets=["webhook-off", "nope-1"])
    await _settle(dispatcher)
    assert transport.calls == []


# -- rules ----------------------------------------------------------------------


RULES = {
    "version": 1,
    "events": {
        "message.dm": {"channels": ["builtin:push", "ntfy-1"]},
        "message.channel": {"channels": ["builtin:push"]},
        "contact.first_seen": {
            "channels": ["builtin:push", "ntfy-1"],
            "contact_types": {"companion": ["builtin:push"], "repeater": ["ntfy-1"], "sensor": []},
        },
        "radio.offline": {"channels": ["ntfy-1"], "always_deliver": True},
    },
    "conversations": {
        f"contact-{DM_KEY}": {"ntfy-1": False},
        "channel-PRIVATE": {"ntfy-1": True},
    },
}


def test_conversation_policy_generalises_the_push_policy():
    assert message_channel_ids(
        RULES, state_key="contact-other", is_dm=True, is_public_or_hashtag=False
    ) == ["builtin:push", "ntfy-1"]
    # A stored exception wins, either way.
    assert message_channel_ids(
        RULES, state_key=f"contact-{DM_KEY}", is_dm=True, is_public_or_hashtag=False
    ) == ["builtin:push"]
    assert message_channel_ids(
        RULES, state_key="channel-PUBLIC", is_dm=False, is_public_or_hashtag=True
    ) == ["builtin:push"]
    # A channel with its own key notifies only through an exception.
    assert (
        message_channel_ids(
            RULES, state_key="channel-OTHER", is_dm=False, is_public_or_hashtag=False
        )
        == []
    )
    assert message_channel_ids(
        RULES, state_key="channel-PRIVATE", is_dm=False, is_public_or_hashtag=False
    ) == ["ntfy-1"]


@pytest.mark.parametrize(
    ("contact_type", "expected"),
    [(1, ["builtin:push"]), (2, ["ntfy-1"]), (3, []), (4, []), (0, [])],
)
def test_first_seen_follows_the_contact_type(contact_type, expected):
    event = event_from_payload({"event": "first_seen", "contact_type": contact_type})
    assert event is not None
    assert event_channel_ids(RULES, event) == expected


@pytest.mark.asyncio
async def test_emit_without_targets_follows_the_rules(test_db):
    await _add_channel("ntfy-1", "ntfy")
    await _set_rules(RULES)
    transport = StubTransport()
    dispatcher = _dispatcher(transport, FakeClock())
    await dispatcher.emit(event_from_payload(_dm()))
    await dispatcher.emit(event_from_payload({"event": "radio_offline"}))
    await _settle(dispatcher)
    assert sorted((c[0], c[1]) for c in transport.calls) == [
        ("builtin:push", "message.dm"),
        ("ntfy-1", "radio.offline"),
    ]


# -- status in health ----------------------------------------------------------


@pytest.mark.asyncio
async def test_health_status_changes_broadcast_health(test_db, monkeypatch):
    await _add_channel("webhook-1", "webhook")
    broadcasts: list[bool] = []
    monkeypatch.setattr(
        "app.websocket.broadcast_health", lambda *args, **kwargs: broadcasts.append(True)
    )
    transport = StubTransport(
        script=[SendResult("failed", error_code="http_400")] * 2 + [SendResult("ok")]
    )
    dispatcher = _dispatcher(transport, FakeClock())

    async def send_one() -> dict:
        await dispatcher.emit(None, payload={"event": "oss_update"}, targets=["webhook-1"])
        await _settle(dispatcher)
        return (await dispatcher.statuses())["webhook-1"]

    status = await send_one()
    assert (status["status"], status["last_error"]) == ("error", "http_400")
    assert len(broadcasts) == 1
    await send_one()  # same status, same error: nothing to say
    assert len(broadcasts) == 1
    status = await send_one()
    assert (status["status"], status["last_error"]) == ("connected", None)
    assert status["last_ok_at"] is not None
    assert len(broadcasts) == 2


@pytest.mark.asyncio
async def test_health_lists_every_channel_with_its_status(test_db, client):
    await _add_channel("webhook-off", "webhook", enabled=0)
    await _add_channel("ntfy-paused", "ntfy", paused_until=int(time.time()) + 600)
    body = (await client.get("/api/health")).json()
    statuses = body["notification_statuses"]
    assert statuses["builtin:push"] == {
        "status": "connected",
        "last_error": None,
        "last_ok_at": None,
    }
    assert statuses["webhook-off"]["status"] == "disconnected"
    assert statuses["ntfy-paused"]["status"] == "paused"


# -- shutdown -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_records_pending_jobs_as_aborted(test_db, monkeypatch):
    from app import background_tasks

    await _add_channel("webhook-1", "webhook")
    gate = asyncio.Event()  # never set: the first send hangs
    transport = StubTransport(gate=gate)
    dispatcher = _dispatcher(transport, FakeClock())
    for _ in range(3):
        await dispatcher.emit(None, payload={"event": "oss_update"}, targets=["webhook-1"])
    for _ in range(50):
        if transport.calls:
            break
        await asyncio.sleep(0.01)
    await background_tasks.drain_background_tasks(timeout=2.0)
    rows = await _deliveries()
    assert [r["status"] for r in rows] == ["aborted"] * 3


@pytest.mark.asyncio
async def test_shutdown_aborts_a_pending_long_retry(test_db):
    from app import background_tasks

    await _add_channel("webhook-1", "webhook")
    transport = StubTransport(script=[SendResult("retry", error_code="timeout")])

    async def no_wait(seconds: float) -> None:
        if seconds == LONG_RETRY_S:
            await asyncio.Event().wait()  # the five minutes never end

    dispatcher = Dispatcher(sleep=no_wait, transport_for=lambda _c: transport)
    await dispatcher.emit(None, payload={"event": "radio_offline"}, targets=["webhook-1"])
    await dispatcher.wait_idle()
    await background_tasks.drain_background_tasks(timeout=2.0)
    rows = await _deliveries()
    assert [(r["status"], r["attempts"]) for r in rows] == [("aborted", 3)]


# -- the legacy adapter ---------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_maps_media_to_legacy_channels(test_db, monkeypatch):
    from app import notify
    from app.models import NotificationDestinations

    seen: list[tuple[list[str], dict]] = []

    async def fake_emit(event, *, payload=None, targets=None):
        seen.append((list(targets or []), dict(payload or {})))

    monkeypatch.setattr(dispatcher_module, "emit", fake_emit)
    dest = NotificationDestinations.model_validate(
        {"email": {"host": "smtp.example.org", "to": "a@example.org"}}
    )
    monkeypatch.setattr(
        "app.notify.AppSettingsRepository.get",
        AsyncMock(return_value=type("S", (), {"notification_destinations": dest})()),
    )
    await notify.dispatch_system_event(
        {"event": "oss_update"}, {"push": True, "email": True, "webhook": True, "apprise": True}
    )
    # The webhook and Apprise have no destination: skipped, as before the dispatcher.
    assert seen == [(["builtin:push", "legacy:email"], {"event": "oss_update"})]


@pytest.mark.asyncio
async def test_adapter_delivers_web_push_through_the_dispatcher(test_db, monkeypatch):
    from app import notify
    from app.push.manager import PushOutcome, push_manager

    sent: list[dict] = []

    async def fake_dispatch_event(data):
        sent.append(dict(data))
        return PushOutcome(total=1, delivered=1)

    monkeypatch.setattr(push_manager, "dispatch_event", fake_dispatch_event)
    await notify.dispatch_system_event({"event": "app_started", "version": "5"}, {"push": True})
    await notification_dispatcher().wait_idle()
    assert sent == [{"event": "app_started", "version": "5"}]


@pytest.mark.asyncio
async def test_web_push_with_every_subscription_failing_is_an_error(test_db, monkeypatch):
    from app.notifications.transports.web_push import WebPushTransport
    from app.push.manager import PushOutcome, push_manager

    monkeypatch.setattr(
        push_manager, "dispatch_event", AsyncMock(return_value=PushOutcome(total=2, delivered=0))
    )
    channel = Channel(id="builtin:push", type="web_push", name="Web Push")
    result = await WebPushTransport().send(channel, None, Delivery("app.started"), {})
    assert result == SendResult("failed", error_code="push_delivery_failed")
    monkeypatch.setattr(
        push_manager, "dispatch_event", AsyncMock(return_value=PushOutcome(total=0))
    )
    assert (await WebPushTransport().send(channel, None, Delivery("app.started"), {})).ok
