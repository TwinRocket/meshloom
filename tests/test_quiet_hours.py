"""Quiet hours (MSHL-124): window math, suppression, bypasses, counters and the digest.

The window and the digest run on a fake wall clock (``Dispatcher(wall_clock=...)``) and a fixed
host time zone; the dispatcher is driven by hand (``quiet_tick``), never by its 60 s loop.
"""

import asyncio
import json
import time
from datetime import UTC, datetime

import pytest

from app.notifications import quiet_hours as qh
from app.notifications.channels import Channel
from app.notifications.dispatcher import Dispatcher
from app.notifications.events import event_from_payload
from app.notifications.render import build_push_payload, event_notification_text
from app.notifications.transports.base import Delivery, SendResult
from app.notifications.user_rules import PushAudience
from app.repository.notification_channels import invalidate_cache

CHAN_KEY = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"
DM_KEY = "ab" * 32
SECRET_TEXT = "meet at the old mill at 9"
ON = {"enabled": True, "start": "22:00", "end": "07:00", "weekdays": list(range(7))}


@pytest.fixture(autouse=True)
def _paris(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset")
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


class StubTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.payloads: list[dict] = []

    async def send(self, channel, rendered, event, secrets) -> SendResult:
        self.calls.append((channel.id, event.name))
        self.payloads.append(dict(event.payload))
        return SendResult("ok")


@pytest.fixture(autouse=True)
def _cache():
    invalidate_cache()
    yield
    invalidate_cache()


def _dm(n: int = 0) -> dict:
    return {
        "event": "message",
        "type": "PRIV",
        "conversation_key": DM_KEY,
        "text": SECRET_TEXT,
        "sender_name": "Alice",
        "id": n,
    }


async def _add_channel(db, channel_id: str, type_: str, exempt: int = 0) -> None:
    async with db.tx() as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO notification_channels "
            "(id, type, name, enabled, config, secrets, created_at, quiet_hours_exempt) "
            "VALUES (?, ?, ?, 1, '{}', '{}', 1, ?)",
            (channel_id, type_, channel_id, exempt),
        )
    invalidate_cache()


async def _set_rules(db, rules: dict) -> None:
    async with db.tx() as conn:
        await conn.execute(
            "UPDATE app_settings SET notification_rules = ? WHERE id = 1", (json.dumps(rules),)
        )


async def _settle(dispatcher: Dispatcher) -> None:
    await dispatcher.wait_idle()
    await asyncio.sleep(0)
    await dispatcher.wait_idle()


async def _counters(db) -> dict[str, str]:
    async with db.readonly() as conn:
        async with conn.execute(
            "SELECT key, value FROM notification_state WHERE key LIKE 'quiet:%'"
        ) as cursor:
            return dict(await cursor.fetchall())


async def _counters_of(db, prefix: str) -> dict[str, str]:
    async with db.readonly() as conn:
        async with conn.execute(
            "SELECT key, value FROM notification_state WHERE key LIKE ?", (f"{prefix}%",)
        ) as cursor:
            return dict(await cursor.fetchall())


async def _setup(db) -> None:
    await _add_channel(db, "email-1", "email")
    await _add_channel(db, "webhook-1", "webhook", exempt=1)
    await _set_rules(db, {"quiet_hours": {**ON}})


def at(day: int, hour: int, minute: int = 0) -> float:
    """Epoch of 2026-11-<day> hour:minute in the host zone (2026-11-02 is a Monday)."""
    return datetime(2026, 11, day, hour, minute).timestamp()


class Wall:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# -- window math --------------------------------------------------------------


def test_window_inside_outside_overnight_and_weekdays():
    rules = {"quiet_hours": {**ON}}
    assert qh.is_quiet(rules, at(2, 23))  # Monday 23:00
    assert qh.is_quiet(rules, at(3, 6, 59))  # the overnight window runs to Tuesday 07:00
    assert not qh.is_quiet(rules, at(3, 7))
    assert not qh.is_quiet(rules, at(2, 12))
    day = {"quiet_hours": {**ON, "start": "09:00", "end": "17:00", "weekdays": [0]}}
    assert qh.is_quiet(day, at(2, 10))
    assert not qh.is_quiet(day, at(3, 10))  # Tuesday is not selected
    assert not qh.is_quiet({"quiet_hours": {**ON, "enabled": False}}, at(2, 23))
    assert not qh.is_quiet({}, at(2, 23))
    # An overnight window belongs to the weekday it opened on: Monday only.
    monday = {"quiet_hours": {**ON, "weekdays": [0]}}
    assert qh.is_quiet(monday, at(3, 2)) and not qh.is_quiet(monday, at(4, 2))


def test_validation_names_the_bad_field():
    for bad, field in (
        ({"start": "25:00"}, "start"),
        ({"end": "7"}, "end"),
        ({"weekdays": [7]}, "weekdays"),
        ({"weekdays": [True]}, "weekdays"),
        ({"enabled": "yes"}, "enabled"),
        ({"nope": 1}, "nope"),
    ):
        with pytest.raises(ValueError) as caught:
            qh.validate_quiet_hours(bad)
        assert caught.value.args[0] == field
    assert qh.validate_quiet_hours({"weekdays": [2, 0, 2]})["weekdays"] == [0, 2]
    assert qh.quiet_hours_of({"quiet_hours": {"start": "bad"}}) == qh.DEFAULT_QUIET_HOURS


# -- suppression --------------------------------------------------------------


@pytest.mark.asyncio
async def test_suppressed_exempt_and_urgent(test_db):
    await _setup(test_db)
    wall = Wall(at(2, 23))
    transport = StubTransport()
    dispatcher = Dispatcher(transport_for=lambda _c: transport, wall_clock=wall)
    targets = ["email-1", "webhook-1"]
    await dispatcher.emit(None, payload=_dm(), targets=targets)
    await _settle(dispatcher)
    assert [c[0] for c in transport.calls] == ["webhook-1"]  # exempt channel delivered
    # radio.offline is urgent: it reaches the channel that is not exempt too.
    transport.calls.clear()
    await dispatcher.emit(None, payload={"event": "radio_offline"}, targets=["email-1"])
    await _settle(dispatcher)
    assert [c[1] for c in transport.calls] == ["radio.offline"]
    # always_deliver on an event bypasses as well.
    await _set_rules(
        test_db, {"quiet_hours": {**ON}, "events": {"message.dm": {"always_deliver": True}}}
    )
    transport.calls.clear()
    await dispatcher.emit(None, payload=_dm(n=1), targets=["email-1"])
    await _settle(dispatcher)
    assert len(transport.calls) == 1
    # Outside the window nothing is held.
    wall.now = at(2, 12)
    transport.calls.clear()
    await _set_rules(test_db, {"quiet_hours": {**ON}})
    await dispatcher.emit(None, payload=_dm(n=2), targets=["email-1"])
    await _settle(dispatcher)
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_one_update_per_event_and_no_text_stored(test_db):
    db = test_db
    await _setup(test_db)
    wall = Wall(at(2, 23))
    dispatcher = Dispatcher(transport_for=lambda _c: StubTransport(), wall_clock=wall)
    await dispatcher.emit(None, payload=_dm(n=0), targets=["email-1"])  # first: INSERT
    statements: list[str] = []
    conn = db._connection
    await conn.set_trace_callback(statements.append)
    try:
        for n in range(1, 6):
            await dispatcher.emit(None, payload=_dm(n=n), targets=["email-1"])
    finally:
        await conn.set_trace_callback(None)
    # The journal rows queue their own 24 h tallies (``stat:`` keys, MSHL-390): not counted here.
    state = [s for s in statements if "notification_state" in s and "stat:" not in s]
    assert len(state) == 5 and all(s.startswith("UPDATE") for s in state)
    [(key, value)] = (await _counters(test_db)).items()
    assert key == f"quiet:email-1:message.dm:contact-{DM_KEY}" and value == "6"
    assert SECRET_TEXT not in key + value
    await dispatcher.wait_idle()
    async with db.readonly() as conn:
        async with conn.execute("SELECT status FROM notification_deliveries") as cursor:
            rows = [{"status": r[0]} for r in await cursor.fetchall()]
    assert {r["status"] for r in rows} == {"suppressed_quiet"}


@pytest.mark.asyncio
async def test_digest_content_reset_and_restart(test_db):
    db = test_db
    await _setup(test_db)
    async with db.tx() as conn:
        await conn.execute("INSERT INTO contacts (public_key, name) VALUES (?, 'Léa')", (DM_KEY,))
        await conn.execute(
            "INSERT INTO channels (key, name, is_hashtag) VALUES (?, '#paris', 1)",
            ("AA" * 16,),
        )
    wall = Wall(at(2, 23))
    first = Dispatcher(transport_for=lambda _c: StubTransport(), wall_clock=wall)
    for n in range(3):
        await first.emit(None, payload=_dm(n=n), targets=["email-1"])
    for n in range(9):
        await first.emit(
            None,
            payload={
                "event": "message",
                "type": "CHAN",
                "conversation_key": "AA" * 16,
                "channel_name": "#paris",
                "text": f"hello {n}",
                "sender_name": "Bob",
                "id": 100 + n,
            },
            targets=["email-1"],
        )
    await first.emit(
        None,
        payload={"event": "first_seen", "public_key": "cd" * 32, "name": "N", "contact_type": 1},
        targets=["email-1"],
    )
    await first.wait_idle()

    # A restart in the middle of the window keeps the counts; nothing is sent yet.
    transport = StubTransport()
    second = Dispatcher(transport_for=lambda _c: transport, wall_clock=wall)
    assert await second.quiet_tick() == 0 and transport.calls == []
    assert len(await _counters(test_db)) == 3

    wall.now = at(3, 7, 1)
    assert await second.quiet_tick() == 1
    await _settle(second)
    [payload] = transport.payloads
    assert payload["count"] == 12 and payload["conversations"] == 2 and payload["contacts"] == 1
    assert payload["items"] == [["#paris", 9], ["Léa", 3]] and "dms" not in payload
    assert SECRET_TEXT not in json.dumps(payload)
    assert await _counters(test_db) == {}  # cleared
    assert await second.quiet_tick() == 0  # and nothing is sent twice


def test_digest_renders_fr_en_without_text():
    payload = qh.build_digest(
        [
            qh.QuietRow("message.channel", "channel-A", 9),
            qh.QuietRow("message.dm", "contact-B", 3),
            qh.QuietRow("message.channel", "channel-C", 2),
            qh.QuietRow("contact.first_seen", "", 1),
        ],
        {
            "channel-A": qh.Label("#paris", False, True),
            "contact-B": qh.Label("Léa", True),
            "channel-C": qh.Label("#test", False, True),
        },
        names=True,
    )
    assert payload is not None
    title, body = event_notification_text(payload, "en", include_message_text="never")
    assert title == "Quiet hours summary"
    assert body == (
        "During quiet hours: 14 messages in 3 conversations (#paris 9, Léa 3, #test 2), "
        "1 new contact"
    )
    _, fr = event_notification_text(payload, "fr")
    assert fr.startswith("Pendant les heures calmes : 14 messages dans 3 conversations")
    assert fr.endswith("1 nouveau contact")
    assert json.loads(build_push_payload(payload, "en"))["body"] == body
    # Without the name policy a DM is only counted; a Public/# channel stays named.
    hidden = qh.build_digest(
        [qh.QuietRow("message.dm", "contact-B", 3), qh.QuietRow("message.channel", "channel-A", 1)],
        {"channel-A": qh.Label("#paris", False, True), "contact-B": qh.Label("Léa", True)},
        names=False,
    )
    assert hidden is not None and hidden["dms"] == 3 and hidden["items"] == [["#paris", 1]]
    assert "Léa" not in json.dumps(hidden)
    assert qh.build_digest([], {}, names=True) is None


@pytest.mark.asyncio
async def test_no_digest_when_disabled_and_held_during_a_pause(test_db):
    await _setup(test_db)
    wall = Wall(at(2, 23))
    transport = StubTransport()
    dispatcher = Dispatcher(transport_for=lambda _c: transport, wall_clock=wall)
    await dispatcher.emit(None, payload=_dm(), targets=["email-1"])
    wall.now = at(3, 8)
    await _set_rules(test_db, {"quiet_hours": {**ON}, "paused_until": int(at(3, 12))})
    assert await dispatcher.quiet_tick() == 0 and len(await _counters(test_db)) == 1  # held
    await _set_rules(test_db, {"quiet_hours": {**ON, "digest_at_end": False}})
    assert await dispatcher.quiet_tick() == 0 and await _counters(test_db) == {}  # cleared, silent
    await _settle(dispatcher)
    assert transport.calls == []
    # digest_at_end off counts nothing during the window either.
    wall.now = at(3, 23)
    await dispatcher.emit(None, payload=_dm(n=3), targets=["email-1"])
    assert await _counters(test_db) == {}


@pytest.mark.asyncio
async def test_push_digest_goes_only_to_wanted_accounts(test_db):
    await _set_rules(test_db, {"quiet_hours": {**ON}})
    wall = Wall(at(2, 23))
    transport = StubTransport()
    dispatcher = Dispatcher(transport_for=lambda _c: transport, wall_clock=wall)
    payload = _dm()
    delivery = Delivery(
        "message.dm",
        payload,
        event_from_payload(payload),
        PushAudience(default=None, per_user={7: payload}),
    )
    push = Channel(id="builtin:push", type="web_push", name="Web Push")
    await dispatcher._suppress_quiet(push, delivery, {"quiet_hours": {**ON}})
    assert list(await _counters(test_db)) == [f"quiet:builtin:push@7:message.dm:contact-{DM_KEY}"]
    wall.now = at(3, 8)
    built = await dispatcher._digest(
        push,
        {7: [qh.QuietRow("message.dm", f"contact-{DM_KEY}", 1)]},
        {},
    )
    assert built is not None and built.audience is not None
    assert built.audience.default is None and built.audience.per_user[7]["count"] == 1


@pytest.mark.asyncio
async def test_a_re_emitted_event_is_counted_once(test_db):
    await _setup(test_db)
    wall = Wall(at(2, 23))
    dispatcher = Dispatcher(transport_for=lambda _c: StubTransport(), wall_clock=wall)
    for _ in range(3):  # the same event (same dedup key) arriving again
        await dispatcher.emit(None, payload=_dm(n=5), targets=["email-1"])
    await dispatcher.emit(None, payload=_dm(n=6), targets=["email-1"])
    assert list((await _counters(test_db)).values()) == ["2"]
    # Counted while held, so the same event after the window is no new message either.
    wall.now = at(3, 12)
    transport = StubTransport()
    after = Dispatcher(transport_for=lambda _c: transport, wall_clock=wall)
    assert await after.quiet_tick() == 1
    await _settle(after)
    assert transport.payloads[0]["count"] == 2


@pytest.mark.asyncio
async def test_pause_digest_is_sent_at_resume(test_db):
    await _setup(test_db)
    await _set_rules(test_db, {"paused_until": int(at(2, 15))})  # no quiet hours: a plain pause
    wall = Wall(at(2, 12))
    transport = StubTransport()
    dispatcher = Dispatcher(transport_for=lambda _c: transport, wall_clock=wall)
    await dispatcher.emit(None, payload=_dm(n=1), targets=["email-1"])
    await dispatcher.emit(None, payload=_dm(n=2), targets=["email-1"])
    await dispatcher.emit(None, payload=_dm(n=2), targets=["email-1"])  # a duplicate
    await _settle(dispatcher)
    assert transport.calls == []
    assert list((await _counters_of(test_db, "pause:")).values()) == ["2"]
    assert await dispatcher.quiet_tick() == 0  # still paused: counters kept
    assert len(await _counters_of(test_db, "pause:")) == 1
    wall.now = at(2, 15, 1)
    assert await dispatcher.quiet_tick() == 1
    await _settle(dispatcher)
    [payload] = transport.payloads
    assert payload["scope"] == "pause" and payload["count"] == 2
    title, _ = event_notification_text(payload, "en", include_message_text="never")
    assert title == "Summary of the pause"
    assert await _counters_of(test_db, "pause:") == {}
    assert await dispatcher.quiet_tick() == 0


@pytest.mark.asyncio
async def test_a_dropped_digest_is_put_back_and_given_up_after_a_bound(test_db, monkeypatch):
    from app.notifications import dispatcher as dmod

    await _setup(test_db)
    wall = Wall(at(2, 23))
    transport = StubTransport()
    dispatcher = Dispatcher(transport_for=lambda _c: transport, wall_clock=wall)
    await dispatcher.emit(None, payload=_dm(n=1), targets=["email-1"])
    wall.now = at(3, 8)
    dropped = {"on": True}
    real = dispatcher._admit

    async def admit(channel, job, rules):
        return False if dropped["on"] else await real(channel, job, rules)

    monkeypatch.setattr(dispatcher, "_admit", admit)
    assert await dispatcher.quiet_tick() == 0
    assert list((await _counters(test_db)).values()) == ["1"]  # kept for the next tick
    dropped["on"] = False
    assert await dispatcher.quiet_tick() == 1
    await _settle(dispatcher)
    assert transport.payloads[0]["count"] == 1 and await _counters(test_db) == {}
    # Still dropped after QUIET_DIGEST_RETRIES ticks: given up, nothing left behind.
    wall.now = at(3, 23)
    await dispatcher.emit(None, payload=_dm(n=2), targets=["email-1"])
    wall.now = at(4, 8)
    dropped["on"] = True
    for _ in range(dmod.QUIET_DIGEST_RETRIES):
        assert await dispatcher.quiet_tick() == 0
        assert len(await _counters(test_db)) == 1
    assert await dispatcher.quiet_tick() == 0
    assert await _counters(test_db) == {}


@pytest.mark.asyncio
async def test_a_digest_waits_out_a_long_channel_pause_without_burning_retries(test_db):
    from app.notifications import dispatcher as dmod

    await _setup(test_db)
    wall = Wall(at(2, 23))
    transport = StubTransport()
    dispatcher = Dispatcher(transport_for=lambda _c: transport, wall_clock=wall)
    await dispatcher.emit(None, payload=_dm(n=1), targets=["email-1"])
    wall.now = at(3, 8)
    async with test_db.tx() as conn:
        await conn.execute(
            "UPDATE notification_channels SET paused_until = ? WHERE id = 'email-1'",
            (int(at(3, 8)) + 3600,),
        )
    invalidate_cache()
    for _ in range(dmod.QUIET_DIGEST_RETRIES * 3):  # far past the retry budget
        assert await dispatcher.quiet_tick() == 0
        assert list((await _counters(test_db)).values()) == ["1"]
        assert dispatcher._digest_retries == {}  # a pause spends nothing
    wall.now = at(3, 9, 1)
    assert await dispatcher.quiet_tick() == 1
    await _settle(dispatcher)
    assert transport.payloads[0]["count"] == 1 and await _counters(test_db) == {}
    assert dispatcher._digest_retries == {}


@pytest.mark.asyncio
async def test_retry_budget_is_reset_after_a_success_or_when_counters_are_gone(
    test_db, monkeypatch
):
    from app.notifications import dispatcher as dmod

    await _setup(test_db)
    wall = Wall(at(2, 23))
    transport = StubTransport()
    dispatcher = Dispatcher(transport_for=lambda _c: transport, wall_clock=wall)
    await dispatcher.emit(None, payload=_dm(n=1), targets=["email-1"])
    wall.now = at(3, 8)
    dropped = {"on": True}
    real = dispatcher._admit

    async def admit(channel, job, rules):
        return False if dropped["on"] else await real(channel, job, rules)

    monkeypatch.setattr(dispatcher, "_admit", admit)
    for _ in range(dmod.QUIET_DIGEST_RETRIES - 1):
        assert await dispatcher.quiet_tick() == 0
    assert list(dispatcher._digest_retries.values()) == [dmod.QUIET_DIGEST_RETRIES - 1]
    dropped["on"] = False
    assert await dispatcher.quiet_tick() == 1  # success: the budget is reset
    assert dispatcher._digest_retries == {}
    # A second digest gets the full budget again: it survives QUIET_DIGEST_RETRIES refusals.
    wall.now = at(3, 23)
    await dispatcher.emit(None, payload=_dm(n=2), targets=["email-1"])
    wall.now = at(4, 8)
    dropped["on"] = True
    for _ in range(dmod.QUIET_DIGEST_RETRIES):
        assert await dispatcher.quiet_tick() == 0
        assert len(await _counters(test_db)) == 1
    # Counters deleted behind its back (channel removed): no stale retry state is left.
    async with test_db.tx() as conn:
        await conn.execute("DELETE FROM notification_state WHERE key LIKE 'quiet:%'")
    assert await dispatcher.quiet_tick() == 0
    assert dispatcher._digest_retries == {}


@pytest.mark.asyncio
async def test_a_retryable_digest_failure_gets_the_long_retry(test_db):
    from app.notifications.dispatcher import LONG_RETRY_EVENTS

    assert qh.DIGEST_EVENT in LONG_RETRY_EVENTS


@pytest.mark.asyncio
async def test_start_runs_the_loop_before_any_event(test_db):
    dispatcher = Dispatcher(transport_for=lambda _c: StubTransport())
    assert dispatcher._quiet_task is None
    dispatcher.start()
    task = dispatcher._quiet_task
    assert task is not None and not task.done()
    dispatcher.start()
    assert dispatcher._quiet_task is task  # idempotent
    task.cancel()


def utc(month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC).timestamp()


def test_window_follows_the_host_wall_clock_across_dst():
    # Europe/Paris: fall back on 2026-10-25 (03:00 CEST -> 02:00 CET), forward on 2026-03-29.
    overnight = {"quiet_hours": {**ON}}
    assert qh.is_quiet(overnight, utc(10, 25, 5, 59))  # 06:59 CET
    assert not qh.is_quiet(overnight, utc(10, 25, 6))  # 07:00 CET: the window closes
    assert qh.is_quiet(overnight, utc(3, 29, 4, 59))  # 06:59 CEST
    assert not qh.is_quiet(overnight, utc(3, 29, 5))  # 07:00 CEST
    # A window inside the changed hour follows the clock on the wall: 02:30 happens twice in
    # autumn (both inside), and the missing hour of spring is skipped, not bridged.
    inside = {"quiet_hours": {**ON, "start": "02:30", "end": "03:30"}}
    assert qh.is_quiet(inside, utc(10, 25, 0, 30))  # 02:30 CEST
    assert qh.is_quiet(inside, utc(10, 25, 1, 30))  # 02:30 CET, the repeated hour
    assert not qh.is_quiet(inside, utc(10, 25, 2, 30))  # 03:30 CET
    assert not qh.is_quiet(inside, utc(3, 29, 0, 59))  # 01:59 CET
    assert qh.is_quiet(inside, utc(3, 29, 1, 0))  # 03:00 CEST, after the gap


# -- API and backup -----------------------------------------------------------


async def test_rules_api_quiet_hours_and_timezone(test_db, client):
    view = (await client.get("/api/notifications/rules")).json()
    assert view["quiet_hours"] == qh.DEFAULT_QUIET_HOURS and view["host_timezone"]
    patched = await client.patch(
        "/api/notifications/rules", json={"quiet_hours": {"enabled": True, "start": "23:30"}}
    )
    assert patched.status_code == 200
    assert patched.json()["quiet_hours"] == {
        **qh.DEFAULT_QUIET_HOURS,
        "enabled": True,
        "start": "23:30",
    }
    bad = await client.patch("/api/notifications/rules", json={"quiet_hours": {"end": "99:00"}})
    assert bad.status_code == 400
    assert bad.json()["detail"]["params"]["fields"] == ["quiet_hours.end"]
    bad = await client.patch("/api/notifications/rules", json={"quiet_hours": {"weekdays": [9]}})
    assert bad.status_code == 400


def test_backup_rules_keep_valid_quiet_hours_and_drop_bad():
    from app.services.backup_notifications import sanitize_rules

    warnings: list[str] = []
    kept = sanitize_rules({"quiet_hours": {"enabled": True, "weekdays": [1]}}, warnings)
    assert kept["quiet_hours"]["enabled"] is True and kept["quiet_hours"]["weekdays"] == [1]
    assert warnings == []
    dropped = sanitize_rules({"quiet_hours": {"start": "x"}}, warnings)
    assert "quiet_hours" not in dropped and warnings == ["notification_rules_sanitized"]
