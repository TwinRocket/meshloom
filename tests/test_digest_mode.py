"""Per-channel digest mode (MSHL-136): counts, interval boundary, bypasses, restart safety.

Driven on a fake wall clock (``Dispatcher(wall_clock=...)``) and by hand (``digest_tick``), never
by the 60 s loop. The quiet-hours counters and renderer are reused (``test_quiet_hours.py``).
"""

import asyncio
import ipaddress
import json
import socket
import time
from datetime import datetime

import pytest

from app.notifications import quiet_hours as qh
from app.notifications.channels import ChannelCreate, ChannelUpdate
from app.notifications.dispatcher import Dispatcher
from app.notifications.render import event_notification_text
from app.notifications.transports.base import SendResult
from app.repository.notification_channels import NotificationChannelRepository as Channels
from app.repository.notification_channels import invalidate_cache
from app.services.backup_notifications import export_notifications, restore_notifications

DM_KEY = "ab" * 32
CHAN_KEY = "AA" * 16
SECRET_TEXT = "meet at the old mill at 9"
T0 = datetime(2026, 11, 2, 12, 0).timestamp()


@pytest.fixture(autouse=True)
def _dns(monkeypatch):
    """Host names resolve to a public address (the API checks a destination)."""
    real = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            host = "93.184.216.34"
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.fixture(autouse=True)
def _cache():
    invalidate_cache()
    yield
    invalidate_cache()


class Stub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.payloads: list[dict] = []

    async def send(self, channel, rendered, event, secrets) -> SendResult:
        self.calls.append((channel.id, event.name))
        self.payloads.append(dict(event.payload))
        return SendResult("ok")


class Wall:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _dm(n: int = 0, **extra) -> dict:
    return {
        "event": "message",
        "type": "PRIV",
        "conversation_key": DM_KEY,
        "text": SECRET_TEXT,
        "sender_name": "Alice",
        "id": n,
        **extra,
    }


def _chan(n: int = 0, **extra) -> dict:
    return {
        "event": "message",
        "type": "CHAN",
        "conversation_key": CHAN_KEY,
        "channel_name": "#paris",
        "text": f"hello {n}",
        "sender_name": "Bob",
        "id": 100 + n,
        **extra,
    }


async def _add(db, channel_id: str, interval: int, *, exempt: int = 0, enabled: int = 1) -> None:
    async with db.tx() as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO notification_channels "
            "(id, type, name, enabled, config, secrets, created_at, quiet_hours_exempt, "
            "digest_interval_s) VALUES (?, 'email', ?, ?, '{}', '{}', 1, ?, ?)",
            (channel_id, channel_id, enabled, exempt, interval),
        )
    invalidate_cache()


async def _set_rules(db, rules: dict) -> None:
    async with db.tx() as conn:
        await conn.execute(
            "UPDATE app_settings SET notification_rules = ? WHERE id = 1", (json.dumps(rules),)
        )


async def _state(db, prefix: str) -> dict[str, str]:
    async with db.readonly() as conn:
        async with conn.execute(
            "SELECT key, value FROM notification_state WHERE key LIKE ?", (f"{prefix}%",)
        ) as cursor:
            return dict(await cursor.fetchall())


async def _settle(dispatcher: Dispatcher) -> None:
    await dispatcher.wait_idle()
    await asyncio.sleep(0)
    await dispatcher.wait_idle()


def _make(wall: Wall) -> tuple[Dispatcher, Stub]:
    stub = Stub()
    return Dispatcher(transport_for=lambda _c: stub, wall_clock=wall), stub


@pytest.mark.asyncio
async def test_counts_one_summary_at_the_interval_boundary(test_db):
    await _add(test_db, "email-1", 300)
    wall = Wall(T0)
    dispatcher, stub = _make(wall)
    await dispatcher.emit(None, payload=_dm(1), targets=["email-1"])
    wall.now = T0 + 100  # the window opened at the first event
    await dispatcher.emit(None, payload=_dm(2), targets=["email-1"])
    await dispatcher.emit(None, payload=_chan(1), targets=["email-1"])
    await dispatcher.emit(
        None,
        payload={"event": "first_seen", "public_key": "cd" * 32, "name": "N", "contact_type": 1},
        targets=["email-1"],
    )
    await _settle(dispatcher)
    assert stub.calls == []  # nothing per message
    assert len(await _state(test_db, qh.DIGEST_PREFIX)) == 3

    wall.now = T0 + 299
    assert await dispatcher.digest_tick() == 0
    wall.now = T0 + 300
    assert await dispatcher.digest_tick() == 1
    await _settle(dispatcher)
    [payload] = stub.payloads
    assert payload["event"] == "quiet_digest" and payload["scope"] == "digest"
    assert payload["count"] == 3 and payload["conversations"] == 2 and payload["contacts"] == 1
    assert SECRET_TEXT not in json.dumps(payload)
    assert await _state(test_db, qh.DIGEST_PREFIX) == {}  # counters cleared
    assert await _state(test_db, qh.DIGEST_DUE_PREFIX) == {}  # window closed
    assert await dispatcher.digest_tick() == 0  # nothing is sent twice

    # The next event opens a new window of a full interval.
    wall.now = T0 + 400
    await dispatcher.emit(None, payload=_dm(3), targets=["email-1"])
    wall.now = T0 + 699
    assert await dispatcher.digest_tick() == 0
    wall.now = T0 + 700
    assert await dispatcher.digest_tick() == 1


@pytest.mark.asyncio
async def test_summary_text_says_since_the_last_summary_in_both_languages(test_db):
    await _add(test_db, "email-1", 900)
    wall = Wall(T0)
    dispatcher, stub = _make(wall)
    await dispatcher.emit(None, payload=_dm(1), targets=["email-1"])
    wall.now = T0 + 900
    await dispatcher.digest_tick()
    await _settle(dispatcher)
    data = stub.payloads[0]
    assert "Since the last summary" in event_notification_text(data, "en")[1]
    assert "Depuis le dernier résumé" in event_notification_text(data, "fr")[1]


@pytest.mark.asyncio
async def test_urgent_mention_keyword_and_watched_nodes_bypass(test_db):
    await _add(test_db, "email-1", 3600)
    wall = Wall(T0)
    dispatcher, stub = _make(wall)
    emitted = [
        ({"event": "radio_offline"}, "radio.offline"),  # urgent
        (_dm(5, mention=True), "message.mention"),
        (_chan(6, keyword="alert", keyword_id="k1"), "message.keyword"),
        (
            {"event": "node_seen", "public_key": "ef" * 32, "name": "W", "heard_at": 5},
            "contact.node_seen",
        ),
        (
            {"event": "node_back", "public_key": "ef" * 32, "name": "W", "heard_at": 6},
            "contact.node_back",
        ),
    ]
    for payload, _name in emitted:
        await dispatcher.emit(None, payload=payload, targets=["email-1"])
        await _settle(dispatcher)
    assert sorted(c[1] for c in stub.calls) == sorted(n for _p, n in emitted)
    assert await _state(test_db, "digest") == {}  # none was counted

    # always_deliver on an event bypasses as well.
    await _set_rules(test_db, {"events": {"message.dm": {"always_deliver": True}}})
    stub.calls.clear()
    await dispatcher.emit(None, payload=_dm(7), targets=["email-1"])
    await _settle(dispatcher)
    assert [c[1] for c in stub.calls] == ["message.dm"]
    assert await _state(test_db, "digest") == {}


@pytest.mark.asyncio
async def test_other_channels_and_the_ws_frame_are_unaffected(test_db, monkeypatch):
    await _add(test_db, "email-1", 300)
    await _add(test_db, "email-2", 0)
    frames: list[str] = []
    monkeypatch.setattr(
        "app.notifications.dispatcher.ws_frame.publish",
        lambda event, *a, **k: frames.append(event.event),
    )
    wall = Wall(T0)
    dispatcher, stub = _make(wall)
    await dispatcher.emit(None, payload=_dm(1), targets=["email-1", "email-2"])
    await _settle(dispatcher)
    assert [c[0] for c in stub.calls] == ["email-2"]  # off: one per message
    assert frames == ["message.dm"]  # the Feed's frame fires per event


@pytest.mark.asyncio
async def test_a_re_emitted_event_is_counted_once(test_db):
    await _add(test_db, "email-1", 300)
    dispatcher, stub = _make(Wall(T0))
    for _ in range(3):
        await dispatcher.emit(None, payload=_dm(1), targets=["email-1"])
    assert list((await _state(test_db, qh.DIGEST_PREFIX)).values()) == ["1"]


@pytest.mark.asyncio
async def test_restart_keeps_the_window_and_an_overdue_one_is_sent_on_the_first_tick(test_db):
    await _add(test_db, "email-1", 600)
    wall = Wall(T0)
    first, _ = _make(wall)
    for n in range(4):
        await first.emit(None, payload=_dm(n), targets=["email-1"])

    # Restart inside the window: counts survive, nothing is sent.
    wall.now = T0 + 200
    second, stub = _make(wall)
    assert await second.digest_tick() == 0 and stub.calls == []
    assert list((await _state(test_db, qh.DIGEST_PREFIX)).values()) == ["4"]
    # An event after the restart joins the same window, which keeps its original end.
    await second.emit(None, payload=_dm(9), targets=["email-1"])
    wall.now = T0 + 599
    assert await second.digest_tick() == 0

    # Restart after the end (the app was down at T0 + 600): flushed at the first tick.
    wall.now = T0 + 5000
    third, stub3 = _make(wall)
    assert await third.digest_tick() == 1
    await _settle(third)
    assert stub3.payloads[0]["count"] == 5


@pytest.mark.asyncio
async def test_counters_without_a_due_row_open_a_window_instead_of_leaking(test_db):
    await _add(test_db, "email-1", 300)
    wall = Wall(T0)
    dispatcher, stub = _make(wall)
    await dispatcher.emit(None, payload=_dm(1), targets=["email-1"])
    async with test_db.tx() as conn:
        await conn.execute("DELETE FROM notification_state WHERE key LIKE 'digest_due:%'")
    wall.now = T0 + 10
    other, stub2 = _make(wall)
    assert await other.digest_tick() == 0
    assert list((await _state(test_db, qh.DIGEST_DUE_PREFIX)).values()) == [str(int(T0 + 310))]
    wall.now = T0 + 310
    assert await other.digest_tick() == 1


@pytest.mark.asyncio
async def test_switching_off_flushes_and_disabling_drops(test_db):
    await _add(test_db, "email-1", 3600)
    await _add(test_db, "email-2", 3600)
    wall = Wall(T0)
    dispatcher, stub = _make(wall)
    for channel in ("email-1", "email-2"):
        await dispatcher.emit(None, payload=_dm(1), targets=[channel])
    await _add(test_db, "email-1", 0)  # digest mode switched off
    await _add(test_db, "email-2", 3600, enabled=0)  # channel disabled
    wall.now = T0 + 10
    assert await dispatcher.digest_tick() == 1
    await _settle(dispatcher)
    assert [c[0] for c in stub.calls] == ["email-1"]
    assert await _state(test_db, "digest") == {}


@pytest.mark.asyncio
async def test_held_during_the_global_pause_and_the_channels_own_pause(test_db):
    await _add(test_db, "email-1", 300)
    wall = Wall(T0)
    dispatcher, stub = _make(wall)
    await dispatcher.emit(None, payload=_dm(1), targets=["email-1"])
    wall.now = T0 + 400
    await _set_rules(test_db, {"paused_until": int(T0 + 1000)})
    assert await dispatcher.digest_tick() == 0  # global pause: counters wait
    assert len(await _state(test_db, qh.DIGEST_PREFIX)) == 1
    await _set_rules(test_db, {})
    async with test_db.tx() as conn:
        await conn.execute("UPDATE notification_channels SET paused_until = ?", (int(T0 + 500),))
    invalidate_cache()
    assert await dispatcher.digest_tick() == 0  # the channel's own pause: waits too
    wall.now = T0 + 501
    assert await dispatcher.digest_tick() == 1


@pytest.mark.asyncio
async def test_quiet_hours_win_over_digest_mode(test_db, monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset")
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    try:
        await _add(test_db, "email-1", 300)
        quiet = {"enabled": True, "start": "22:00", "end": "07:00", "weekdays": list(range(7))}
        await _set_rules(test_db, {"quiet_hours": quiet})
        wall = Wall(datetime(2026, 11, 2, 23, 0).timestamp())
        dispatcher, stub = _make(wall)
        await dispatcher.emit(None, payload=_dm(1), targets=["email-1"])
        assert await _state(test_db, qh.DIGEST_PREFIX) == {}  # counted by quiet hours only
        assert len(await _state(test_db, qh.COUNTER_PREFIX)) == 1
        # A window that was open when quiet hours began is held until they end.
        await _set_rules(test_db, {})
        await dispatcher.emit(None, payload=_dm(2), targets=["email-1"])
        await _set_rules(test_db, {"quiet_hours": quiet})
        wall.now += 400
        assert await dispatcher.digest_tick() == 0
        wall.now = datetime(2026, 11, 3, 7, 1).timestamp()
        assert await dispatcher.digest_tick() == 1
    finally:
        monkeypatch.undo()
        time.tzset()


# -- API, validation, backup, migration ---------------------------------------------------


def _body(**kw) -> ChannelCreate:
    return ChannelCreate(
        type="webhook", name="Hook", config={"url": "https://hooks.example.org/in/abc"}, **kw
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [1, 299, 86401, -5])
async def test_the_interval_is_off_or_between_5_min_and_24_h(test_db, interval):
    with pytest.raises(ValueError):
        _body(digest_interval_s=interval)
    with pytest.raises(ValueError):
        ChannelUpdate(digest_interval_s=interval)


@pytest.mark.asyncio
async def test_create_and_update_store_the_interval(test_db):
    channel = await Channels.create(_body())
    assert channel.digest_interval_s == 0
    for value in (300, 86400, 0):
        channel = await Channels.update(channel.id, ChannelUpdate(digest_interval_s=value))
        assert channel.digest_interval_s == value
    channel = await Channels.update(channel.id, ChannelUpdate(name="Renamed"))
    assert channel.digest_interval_s == 0  # untouched by another field


@pytest.mark.asyncio
async def test_backup_keeps_a_valid_interval_and_drops_a_hand_edited_one(backup_db):
    channel = await Channels.create(_body(digest_interval_s=900))
    section = await export_notifications()
    assert section is not None
    [item] = [c for c in section.data["channels"] if c["id"] == channel.id]
    assert item["digest_interval_s"] == 900
    await Channels.delete(channel.id)
    async with backup_db.tx() as conn:
        await restore_notifications(section, conn)
    invalidate_cache()
    restored = await Channels.get(channel.id)
    assert restored is not None and restored.digest_interval_s == 900

    item["digest_interval_s"] = 30
    async with backup_db.tx() as conn:
        warnings = await restore_notifications(section, conn)
    invalidate_cache()
    assert f"notification_digest_dropped:{channel.id}" in warnings
    restored = await Channels.get(channel.id)
    assert restored is not None and restored.digest_interval_s == 0


def test_the_digestible_events():
    for name in ("message.dm", "message.channel", "contact.first_seen"):
        assert qh.digestible(name)
    for name in (
        "message.mention",
        "message.keyword",
        "contact.node_seen",
        "contact.node_back",
        "radio.offline",
        "telemetry.alert",
        "notify.heartbeat",
    ):
        assert not qh.digestible(name)
