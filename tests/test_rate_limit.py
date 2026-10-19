"""Notification limits (MSHL-125): token buckets, the daily quota, aggregation instead of
drops, the global cap and the persisted dedup keys.

Everything runs on a fake clock injected in the dispatcher (``clock``, ``sleep`` and
``wall_clock``); the buckets are ``aiolimiter`` instances on that clock.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest

from app.notifications.channels import Channel, Limits
from app.notifications.dispatcher import GROUPED_EVENT, Dispatcher, notification_dispatcher
from app.notifications.limits import ClockedLimiter, effective_limits, next_utc_midnight
from app.notifications.render import build_push_payload
from app.notifications.transports.base import SendResult
from app.repository.notification_channels import NotificationChannelRepository as Channels
from app.repository.notification_channels import invalidate_cache

pytestmark = pytest.mark.async_notifications

SECRET_TEXT = "meet at the old mill at 9"
PUBLIC_KEY = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"
DM_KEY = "ab" * 32
DAY = 86400
T0 = 20 * DAY + 3600  # an arbitrary UTC day, one hour in


class FakeClock:
    """One time source for the monotonic clock, the wall clock and the sleeps."""

    def __init__(self, now: float = T0) -> None:
        self.now = now
        self.gate = asyncio.Event()
        self.gate.set()

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        await self.gate.wait()  # closed while a burst is being emitted (see ``_burst``)
        self.now += seconds
        await asyncio.sleep(0)

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class StubTransport:
    payloads: list[dict] = field(default_factory=list)

    async def send(self, channel, rendered, event, secrets) -> SendResult:
        self.payloads.append(dict(event.payload))
        return SendResult("ok")

    @property
    def singles(self) -> list[dict]:
        return [p for p in self.payloads if p.get("event") != GROUPED_EVENT]

    @property
    def groups(self) -> list[dict]:
        return [p for p in self.payloads if p.get("event") == GROUPED_EVENT]


db = None


@pytest.fixture(autouse=True)
def _wired(request):
    global db
    invalidate_cache()
    db = request.getfixturevalue("test_db")
    yield
    invalidate_cache()


async def _add_channel(channel_id: str = "webhook-1", type_: str = "webhook", **cols) -> None:
    async with db.tx() as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO notification_channels "
            "(id, type, name, enabled, config, secrets, created_at, rate_per_min, "
            "rate_per_hour, rate_per_day) VALUES (?, ?, ?, 1, ?, '{}', 1, ?, ?, ?)",
            (
                channel_id,
                type_,
                channel_id,
                json.dumps(cols.get("config", {})),
                cols.get("per_min"),
                cols.get("per_hour"),
                cols.get("per_day"),
            ),
        )
    invalidate_cache()


async def _rows() -> list[dict]:
    async with db.readonly() as conn:
        async with conn.execute("SELECT * FROM notification_deliveries ORDER BY id") as cursor:
            return [dict(row) for row in await cursor.fetchall()]


def _statuses(rows: list[dict]) -> list[str]:
    return [r["status"] for r in rows]


def _dispatcher(transport: StubTransport, clock: FakeClock) -> Dispatcher:
    return Dispatcher(
        clock=clock,
        sleep=clock.sleep,
        wall_clock=clock,
        transport_for=lambda _channel: transport,
    )


def _chan(n: int, *, text: str | None = None) -> dict:
    return {
        "event": "message",
        "type": "CHAN",
        "conversation_key": PUBLIC_KEY,
        "text": text if text is not None else f"hello {n}",
        "sender_name": "Bob",
        "channel_name": "Public",
        "id": n,
    }


def _dm(n: int) -> dict:
    return {
        "event": "message",
        "type": "PRIV",
        "conversation_key": DM_KEY,
        "text": SECRET_TEXT,
        "sender_name": "Alice",
        "id": n,
    }


ALERT = {
    "event": "telemetry_alert",
    "rule_id": "battery",
    "public_key": "cd" * 32,
    "name": "N",
    "value": 3.4,
    "threshold": 3.5,
    "op": "lt",
}


async def _emit(dispatcher: Dispatcher, payload: dict, **kwargs) -> int:
    return await dispatcher.emit(None, payload=payload, targets=["webhook-1"], **kwargs)


async def _burst(dispatcher: Dispatcher, clock: FakeClock, payloads: list[dict]) -> None:
    """Emit ``payloads`` with every sleep (so every aggregation window) held until the
    last one is in: the fake sleep is instant, a real window is 60 s."""
    clock.gate.clear()
    try:
        for payload in payloads:
            await _emit(dispatcher, payload)
    finally:
        clock.gate.set()


async def _settle(dispatcher: Dispatcher) -> None:
    for _ in range(10):
        await dispatcher.wait_idle()
        flushers = [
            w.agg_flusher for w in dispatcher._workers.values() if w.agg_flusher is not None
        ]
        if not [f for f in flushers if not f.done()]:
            break
        await asyncio.gather(*flushers, return_exceptions=True)
    await dispatcher.wait_idle()


# -- the buckets ---------------------------------------------------------------------


def test_the_limiter_refills_on_the_injected_clock():
    clock = FakeClock(0.0)
    bucket = ClockedLimiter(20, 60.0, clock)
    for _ in range(20):
        assert bucket.has_capacity()
        bucket._level += 1
    assert not bucket.has_capacity()
    clock.advance(3.0)  # 20 a minute: one token every 3 s
    assert bucket.has_capacity()
    bucket._level += 1
    assert not bucket.has_capacity()
    clock.advance(60.0)
    assert bucket.has_capacity(20)  # fully refilled


@pytest.mark.asyncio
async def test_a_burst_of_50_on_a_20_a_minute_channel_is_20_sends_then_one_summary():
    await _add_channel(per_min=20)
    clock = FakeClock()
    transport = StubTransport()
    dispatcher = _dispatcher(transport, clock)
    await _burst(dispatcher, clock, [_chan(n) for n in range(50)])
    await _settle(dispatcher)

    assert len(transport.singles) == 20
    [summary] = transport.groups
    assert summary == {"event": GROUPED_EVENT, "count": 30, "channels": {"Public": 30}}
    assert transport.payloads[-1] == summary
    rows = await _rows()
    assert _statuses(rows) == ["aggregated"] * 30
    assert await Channels.get("webhook-1") is not None


@pytest.mark.asyncio
async def test_the_summary_counts_and_never_carries_message_text():
    await _add_channel(per_min=1)
    clock = FakeClock()
    transport = StubTransport()
    dispatcher = _dispatcher(transport, clock)
    await _burst(
        dispatcher,
        clock,
        [_chan(0), *[_chan(n, text=SECRET_TEXT) for n in range(1, 4)], _dm(10), _dm(11)],
    )
    await _settle(dispatcher)

    [summary] = transport.groups
    assert summary == {"event": GROUPED_EVENT, "count": 5, "dms": 2, "channels": {"Public": 3}}
    assert SECRET_TEXT not in json.dumps(summary)
    assert "Bob" not in json.dumps(summary)
    title = json.loads(build_push_payload(summary, "en"))["title"]
    assert title == "+5 messages: 3 in Public, 2 DMs"
    assert json.loads(build_push_payload(summary, "fr"))["title"] == (
        "+5 messages: 3 dans Public, 2 MP"
    )
    assert SECRET_TEXT not in json.dumps(await _rows())


@pytest.mark.asyncio
async def test_a_private_channel_is_counted_without_its_name():
    await _add_channel(per_min=1)
    clock = FakeClock()
    transport = StubTransport()
    dispatcher = _dispatcher(transport, clock)
    private = {**_chan(1), "conversation_key": "11" * 16, "channel_name": "secret-club"}
    await _emit(dispatcher, _chan(0))
    await _emit(dispatcher, private)
    await _settle(dispatcher)
    [summary] = transport.groups
    assert summary == {"event": GROUPED_EVENT, "count": 1, "private": 1}
    assert "secret-club" not in json.dumps(summary)


@pytest.mark.asyncio
async def test_buckets_refill_with_time():
    await _add_channel(per_min=2)
    clock = FakeClock()
    transport = StubTransport()
    dispatcher = _dispatcher(transport, clock)
    # The window is long: nothing is summarised until the buckets have room, so the
    # third message is held while the first two take the tokens.
    for n in range(2):
        await _emit(dispatcher, _chan(n))
    await _settle(dispatcher)
    assert len(transport.singles) == 2
    clock.advance(60.0)
    await _emit(dispatcher, _chan(2))
    await _emit(dispatcher, _chan(3))
    await _settle(dispatcher)
    assert len(transport.singles) == 4
    assert transport.groups == []
    assert _statuses(await _rows()) == []


@pytest.mark.asyncio
async def test_the_hourly_bucket_holds_the_summary_until_it_has_room():
    await _add_channel(per_min=100, per_hour=2)
    clock = FakeClock()
    transport = StubTransport()
    dispatcher = _dispatcher(transport, clock)
    await _burst(dispatcher, clock, [_chan(n) for n in range(5)])
    await _settle(dispatcher)
    assert len(transport.singles) == 2
    [summary] = transport.groups
    assert summary["count"] == 3
    # Two tokens an hour: the summary waited for a token to leak back.
    assert clock.now - T0 >= 1800.0


# -- urgent --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_urgent_passes_a_full_bucket_once_per_dedup_key_and_takes_a_token():
    await _add_channel(per_min=2)
    clock = FakeClock()
    transport = StubTransport()
    dispatcher = _dispatcher(transport, clock)
    await _emit(dispatcher, _chan(0))
    await _emit(dispatcher, _chan(1))
    assert not dispatcher._workers["webhook-1"].buckets.has_capacity()  # type: ignore[union-attr]

    await _emit(dispatcher, ALERT)  # a full bucket: the first alert passes
    await _emit(dispatcher, ALERT)  # a flapping sensor: the same key is held
    await _emit(dispatcher, {**ALERT, "public_key": "ef" * 32})  # another sensor passes
    await _settle(dispatcher)

    events = [p["event"] for p in transport.singles]
    assert events.count("telemetry_alert") == 2
    assert len(transport.singles) == 4
    assert _statuses(await _rows()) == ["aggregated"]
    [summary] = transport.groups
    assert summary == {"event": GROUPED_EVENT, "count": 1, "others": 1}


@pytest.mark.asyncio
async def test_urgent_takes_a_token_so_it_cannot_bypass_the_limit():
    await _add_channel(per_min=2)
    clock = FakeClock()
    transport = StubTransport()
    dispatcher = _dispatcher(transport, clock)
    await _emit(dispatcher, ALERT)  # takes one of the two tokens
    await _emit(dispatcher, _chan(0))  # takes the other
    await _emit(dispatcher, _chan(1))  # no token left
    await _settle(dispatcher)
    assert [p["event"] for p in transport.singles] == ["telemetry_alert", "message"]
    assert _statuses(await _rows()) == ["aggregated"]


# -- the daily quota -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reached_daily_quota_stops_sends_until_the_next_day():
    await _add_channel(per_min=100, per_day=3)
    clock = FakeClock()
    transport = StubTransport()
    dispatcher = _dispatcher(transport, clock)
    for n in range(5):
        await _emit(dispatcher, _chan(n))
    await _settle(dispatcher)
    assert len(transport.singles) == 3
    assert transport.groups == []  # a summary would break the quota too
    assert _statuses(await _rows()) == ["quota_exceeded"] * 2

    clock.advance(next_utc_midnight(clock.now) - clock.now - 1)
    await _emit(dispatcher, _chan(10))
    await _settle(dispatcher)
    assert len(transport.singles) == 3  # still the same day

    clock.advance(2)
    for n in range(11, 15):
        await _emit(dispatcher, _chan(n))
    await _settle(dispatcher)
    assert len(transport.singles) == 6  # a new day: 3 more
    assert _statuses(await _rows()) == ["quota_exceeded"] * 4


@pytest.mark.asyncio
async def test_the_daily_count_survives_a_restart():
    await _add_channel(per_min=100, per_day=3)
    clock = FakeClock()
    first = StubTransport()
    dispatcher = _dispatcher(first, clock)
    for n in range(2):
        await _emit(dispatcher, _chan(n))
    await _settle(dispatcher)
    await dispatcher.flush_counters()  # the shutdown flush

    second = StubTransport()
    restarted = _dispatcher(second, clock)
    for n in range(10, 14):
        await _emit(restarted, _chan(n))
    await _settle(restarted)
    assert len(second.singles) == 1  # 2 + 1 = the quota of 3
    assert _statuses(await _rows()) == ["quota_exceeded"] * 3


def test_the_ntfy_sh_daily_default_is_enforced_and_a_self_hosted_server_has_none():
    def ntfy(config: dict, **rates) -> Channel:
        return Channel(id="ntfy-1", type="ntfy", name="n", config=config, **rates)

    hosted = effective_limits(ntfy({}))
    assert hosted == Limits(10, 30, 200)
    assert effective_limits(ntfy({"server": "https://ntfy.sh"})).per_day == 200
    assert effective_limits(ntfy({"server": "https://ntfy.example.org"})).per_day is None
    # The user's own value wins, per window.
    assert effective_limits(ntfy({}, rate_per_day=50)) == Limits(10, 30, 50)


@pytest.mark.asyncio
async def test_a_hosted_ntfy_channel_gets_its_daily_quota_from_the_registry():
    await _add_channel("webhook-1", "ntfy", config={})  # ntfy.sh, no limit of its own
    transport = StubTransport()
    dispatcher = _dispatcher(transport, FakeClock())
    await _emit(dispatcher, _chan(0))
    await _settle(dispatcher)
    buckets = dispatcher._workers["webhook-1"].buckets
    assert buckets is not None
    assert (buckets.limits.per_min, buckets.limits.per_hour, buckets.limits.per_day) == (
        10,
        30,
        200,
    )
    assert buckets.used_today == 1
    # The 201st send of the day is refused before any request.
    buckets.used_today = 200
    await _emit(dispatcher, _chan(1))
    await _settle(dispatcher)
    assert len(transport.singles) == 1
    assert _statuses(await _rows()) == ["quota_exceeded"]


# -- the global cap ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_global_cap_stops_everything_but_records_rows():
    await _add_channel(per_min=1000)
    clock = FakeClock()
    transport = StubTransport()
    dispatcher = _dispatcher(transport, clock)
    rules = {"limits": {"global_per_hour": 5}}
    for n in range(7):
        await _emit(dispatcher, _chan(n), rules=rules)
    await _emit(dispatcher, ALERT, rules=rules)  # urgent too
    await _settle(dispatcher)

    assert len(transport.payloads) == 5
    assert _statuses(await _rows()) == ["rate_limited"] * 3
    assert dispatcher.capped

    clock.advance(3600.0)
    assert not dispatcher.capped
    await _emit(dispatcher, _chan(100), rules=rules)
    await _settle(dispatcher)
    assert len(transport.payloads) == 6


@pytest.mark.asyncio
async def test_the_cap_defaults_to_600_an_hour_and_ignores_a_bad_value():
    await _add_channel(per_min=1000)
    dispatcher = _dispatcher(StubTransport(), FakeClock())
    assert dispatcher._global_cap({}).max_rate == 600
    assert dispatcher._global_cap({"limits": {"global_per_hour": 0}}).max_rate == 600
    assert dispatcher._global_cap({"limits": {"global_per_hour": "x"}}).max_rate == 600
    assert dispatcher._global_cap({"limits": {"global_per_hour": 50}}).max_rate == 50


@pytest.mark.asyncio
async def test_health_carries_notifications_capped(client):
    body = (await client.get("/api/health")).json()
    assert body["notifications_capped"] is False
    await _add_channel(per_min=1000)
    dispatcher = notification_dispatcher()
    rules = {"limits": {"global_per_hour": 1}}
    await dispatcher.emit(None, payload=_chan(0), targets=["webhook-1"], rules=rules)
    assert (await client.get("/api/health")).json()["notifications_capped"] is True
    await dispatcher.wait_idle()


# -- persisted dedup keys ------------------------------------------------------------


FIRST_SEEN = {"event": "first_seen", "public_key": "12" * 32, "contact_type": 1}


@pytest.mark.asyncio
async def test_dedup_keys_survive_a_restart():
    await _add_channel(per_min=1000)
    clock = FakeClock()
    first = StubTransport()
    dispatcher = _dispatcher(first, clock)
    await _emit(dispatcher, FIRST_SEEN)
    await _emit(dispatcher, FIRST_SEEN)  # the same contact, same dispatcher
    await _emit(dispatcher, _chan(1))
    await _emit(dispatcher, _chan(1))  # the same message id
    await _emit(dispatcher, {"event": "radio_offline", "outage_started_at": 5})
    await _settle(dispatcher)
    assert len(first.payloads) == 3
    await dispatcher.flush_counters()

    second = StubTransport()
    restarted = _dispatcher(second, clock)
    await _emit(restarted, FIRST_SEEN)
    await _emit(restarted, _chan(1))
    await _emit(restarted, {"event": "radio_offline", "outage_started_at": 5})
    await _settle(restarted)
    assert second.payloads == []  # all remembered

    await _emit(restarted, {"event": "radio_offline", "outage_started_at": 6})  # a new outage
    await _emit(restarted, {**FIRST_SEEN, "public_key": "34" * 32})  # another contact
    await _settle(restarted)
    assert len(second.payloads) == 2


@pytest.mark.asyncio
async def test_a_first_seen_key_expires_after_a_day():
    await _add_channel(per_min=1000)
    clock = FakeClock()
    transport = StubTransport()
    dispatcher = _dispatcher(transport, clock)
    await _emit(dispatcher, FIRST_SEEN)
    clock.advance(DAY - 10)
    await _emit(dispatcher, FIRST_SEEN)
    await _settle(dispatcher)
    assert len(transport.payloads) == 1
    clock.advance(20)
    await _emit(dispatcher, FIRST_SEEN)
    await _settle(dispatcher)
    assert len(transport.payloads) == 2


@pytest.mark.asyncio
async def test_telemetry_alerts_have_no_persisted_dedup():
    await _add_channel(per_min=1000)
    transport = StubTransport()
    dispatcher = _dispatcher(transport, FakeClock())
    await _emit(dispatcher, ALERT)
    await _emit(dispatcher, ALERT)
    await _settle(dispatcher)
    assert len(transport.payloads) == 2
    async with db.readonly() as conn:
        async with conn.execute("SELECT COUNT(*) FROM notification_state") as cursor:
            assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_a_channel_edit_changes_its_limits_at_once():
    await _add_channel(per_min=1)
    transport = StubTransport()
    clock = FakeClock()
    dispatcher = _dispatcher(transport, clock)
    await _emit(dispatcher, _chan(0))
    await _emit(dispatcher, _chan(1))
    await _settle(dispatcher)
    assert len(transport.singles) == 1
    await _add_channel(per_min=50)
    await _emit(dispatcher, _chan(2))
    await _emit(dispatcher, _chan(3))
    await _settle(dispatcher)
    assert len(transport.singles) == 3
