"""Per-channel heartbeat (MSHL-131): the ``notify.heartbeat`` message every interval, the
healthchecks-style ping URL through the egress guard, the exemptions, the loops' lifecycle,
the auto-pause interaction and the backup round trip. Fake clock and fake sleep throughout."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
from contextlib import asynccontextmanager

import httpx
import pytest

from app.net.egress_guard import PinnedClient
from app.notifications import heartbeat as heartbeat_module
from app.notifications import quiet_hours
from app.notifications import rules as rules_mod
from app.notifications.channels import ChannelCreate, ChannelError, ChannelUpdate
from app.notifications.dispatcher import Dispatcher, notification_dispatcher
from app.notifications.transports.base import SendResult
from app.repository.notification_channels import NotificationChannelRepository as Channels
from app.repository.notification_channels import NotificationJournalRepository as Journal
from app.repository.notification_channels import invalidate_cache
from app.services.backup_notifications import export_notifications, restore_notifications

pytestmark = pytest.mark.async_notifications

T0 = 1_800_000_000
PING_URL = "https://hc-ping.example.org/0b1c2d3e-ping-token-42"
TOKEN = "ping-token-42"


@pytest.fixture(autouse=True)
def _dns(monkeypatch):
    """Host names resolve to a public address, except ``*.lan.example`` (private)."""
    real = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            host = "192.168.1.20" if str(host).endswith("lan.example") else "93.184.216.34"
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.fixture(autouse=True)
def _fresh_cache():
    invalidate_cache()
    yield
    invalidate_cache()


class Clock:
    def __init__(self) -> None:
        self.now = float(T0)

    def __call__(self) -> float:
        return self.now


class Sleeper:
    """Records each sleep, advances the clock, and stops the loop after ``limit`` sleeps."""

    def __init__(self, clock: Clock, limit: int = 3) -> None:
        self.clock = clock
        self.limit = limit
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if len(self.calls) >= self.limit:
            raise asyncio.CancelledError
        self.clock.now += seconds


class Recorder:
    def __init__(self, answer: SendResult | None = None) -> None:
        self.answer = answer or SendResult("ok")
        self.calls: list[tuple[str, str, dict]] = []

    async def send(self, channel, rendered, event, secrets) -> SendResult:
        self.calls.append((channel.id, event.name, dict(event.payload)))
        return self.answer


class Pings:
    """The HTTP side of the ping, through the real ``PinnedClient`` on a mock transport."""

    def __init__(self, status: int = 200, headers: dict | None = None) -> None:
        self.status = status
        self.headers = headers or {}
        self.requests: list[httpx.Request] = []
        self.body_read = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        pings = self

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                pings.body_read = True
                yield b"OK"

        return httpx.Response(self.status, headers=self.headers, stream=Body())


@pytest.fixture
def pings(monkeypatch):
    state = Pings()

    @asynccontextmanager
    async def client(dest, *, verify_tls=True, timeout):
        state.timeout = timeout
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(state.handler), follow_redirects=False
        ) as inner:
            yield PinnedClient(inner, dest)

    monkeypatch.setattr(heartbeat_module, "pinned_async_client", client)
    return state


def _dispatcher(transport, clock: Clock, sleep=None) -> Dispatcher:
    async def no_sleep(_seconds: float) -> None:
        return None

    return Dispatcher(
        clock=clock,
        sleep=sleep or no_sleep,
        wall_clock=clock,
        transport_for=lambda _c: transport,
    )


def _webhook(**extra) -> ChannelCreate:
    return ChannelCreate(
        type="webhook",
        name="Hook",
        config={"url": "https://hooks.example.org/in/abc"},
        **extra,
    )


async def _raw_secrets(test_db, channel_id: str) -> str:
    async with test_db.readonly() as conn:
        async with conn.execute(
            "SELECT secrets FROM notification_channels WHERE id = ?", (channel_id,)
        ) as cursor:
            row = await cursor.fetchone()
    return row[0]


# -- off by default, supported types ---------------------------------------------------


@pytest.mark.asyncio
async def test_off_by_default_and_no_loop(test_db):
    channel = await Channels.create(_webhook())
    assert (channel.heartbeat_interval_s, channel.heartbeat_method) == (0, "GET")
    assert "heartbeat_url" not in channel.secret_fields_set
    dispatcher = _dispatcher(Recorder(), Clock())
    await dispatcher.heartbeats.start()
    try:
        assert dispatcher.heartbeats.running == {}
    finally:
        await dispatcher.heartbeats.stop()


@pytest.mark.asyncio
async def test_an_unbuilt_dispatcher_never_starts_a_loop(test_db):
    await Channels.create(_webhook(heartbeat_interval_s=300))
    dispatcher = _dispatcher(Recorder(), Clock())
    await dispatcher.heartbeats.reconcile()  # not started: the app never called start()
    assert dispatcher.heartbeats.running == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [1, 299, 86401, -5])
async def test_the_interval_is_off_or_between_5_min_and_24_h(test_db, interval):
    with pytest.raises(ValueError):
        _webhook(heartbeat_interval_s=interval)


@pytest.mark.asyncio
async def test_a_type_without_heartbeat_refuses_it(test_db):
    body = ChannelCreate(
        type="email",
        name="Mail",
        config={"host": "smtp.example.org", "to": "me@example.org"},
        heartbeat_interval_s=600,
    )
    with pytest.raises(ChannelError) as refused:
        await Channels.create(body)
    assert refused.value.code == "heartbeat_unsupported"


# -- message mode ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_mode_sends_every_interval_with_instance_uptime_version(test_db):
    channel = await Channels.create(_webhook(heartbeat_interval_s=900))
    clock = Clock()
    sleeper = Sleeper(clock, limit=3)
    transport = Recorder()
    dispatcher = _dispatcher(transport, clock, sleeper)

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.heartbeats._loop(channel.id, 900, False)

    # First beat one interval after start, not at boot (``app.started`` covers that).
    assert sleeper.calls == [900, 900, 900]
    assert [name for _, name, _ in transport.calls] == ["notify.heartbeat", "notify.heartbeat"]
    first, second = (payload for _, _, payload in transport.calls)
    assert first["event"] == "notify.heartbeat"
    assert first["instance"]
    assert isinstance(first["version"], str)
    assert (first["uptime_s"], second["uptime_s"]) == (900, 1800)


@pytest.fixture
def _utc(monkeypatch):
    """Quiet hours read the host's local time: pin it so the window is known around T0."""
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset")
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


async def _beats_while_held(channel_id: str) -> None:
    """Three heartbeats go although the rules hold regular events at the fake clock's now."""
    transport = Recorder()
    dispatcher = _dispatcher(transport, Clock())
    for _ in range(3):
        result = await dispatcher.heartbeats.beat(channel_id)
        assert result is not None and result.ok
    assert [call[1] for call in transport.calls] == ["notify.heartbeat"] * 3
    # Counted, not journaled.
    assert sum(dispatcher._sent.values()) == 3
    assert all(
        row["event"] != "notify.heartbeat"
        for row in await Journal.list_deliveries(channel_id=channel_id)
    )


@pytest.mark.asyncio
async def test_heartbeat_ignores_the_global_pause(test_db):
    channel = await Channels.create(_webhook(heartbeat_interval_s=300))
    rules = await Channels.get_rules()
    rules["paused_until"] = T0 + 3600
    await Channels.set_rules(rules)
    # The pause is active on the fake clock, so a beat that honoured it would not go.
    assert rules_mod.globally_paused(await Channels.get_rules(), T0)
    await _beats_while_held(channel.id)


@pytest.mark.asyncio
async def test_heartbeat_ignores_quiet_hours(test_db, _utc):
    channel = await Channels.create(_webhook(heartbeat_interval_s=300))
    rules = await Channels.get_rules()
    # T0 is 08:00 UTC: the window 07:00-09:00, every day, holds it.
    rules["quiet_hours"] = {
        "enabled": True,
        "start": "07:00",
        "end": "09:00",
        "weekdays": list(range(7)),
    }
    await Channels.set_rules(rules)
    assert quiet_hours.is_quiet(await Channels.get_rules(), T0)
    await _beats_while_held(channel.id)


@pytest.mark.asyncio
async def test_heartbeat_ignores_the_buckets(test_db):
    # One a minute and the fake clock never moves: a send that took a token would stall.
    channel = await Channels.create(_webhook(heartbeat_interval_s=300, rate_per_min=1))
    transport = Recorder()
    dispatcher = _dispatcher(transport, Clock())
    for _ in range(5):
        result = await dispatcher.heartbeats.beat(channel.id)
        assert result is not None and result.ok
    assert len(transport.calls) == 5


@pytest.mark.asyncio
async def test_message_mode_shares_the_channel_auto_pause_and_failures(test_db):
    channel = await Channels.create(_webhook(heartbeat_interval_s=300))
    clock = Clock()
    transport = Recorder(SendResult("failed", error_code="http_404"))
    dispatcher = _dispatcher(transport, clock)

    await dispatcher.heartbeats.beat(channel.id)
    await dispatcher.wait_idle()
    assert dispatcher._live[channel.id].failure_count == 1

    await Channels.set_paused_until(channel.id, int(clock.now) + 600)
    invalidate_cache()
    assert await dispatcher.heartbeats.beat(channel.id) is None
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_a_disabled_channel_beats_no_more(test_db):
    channel = await Channels.create(_webhook(heartbeat_interval_s=300))
    await Channels.update(channel.id, ChannelUpdate(enabled=False))
    transport = Recorder()
    dispatcher = _dispatcher(transport, Clock())
    assert await dispatcher.heartbeats.beat(channel.id) is None
    assert transport.calls == []


# -- ping mode ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_url_is_encrypted_and_masked(test_db, client):
    response = await client.post(
        "/api/notifications/channels",
        json={
            "type": "ntfy",
            "name": "Phone",
            "config": {"topic": "a-b-c-topic-0123456789"},
            "heartbeat_interval_s": 3600,
            "heartbeat_url": PING_URL,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["heartbeat_url"] == "********"
    assert body["heartbeat_interval_s"] == 3600
    assert TOKEN not in response.text
    assert TOKEN not in await _raw_secrets(test_db, body["id"])

    listed = await client.get("/api/notifications/channels")
    assert TOKEN not in listed.text

    # The mask sent back keeps the URL; "" removes it.
    kept = await client.patch(
        f"/api/notifications/channels/{body['id']}",
        json={"heartbeat_url": "********", "name": "Phone 2"},
    )
    assert kept.status_code == 200, kept.text
    found = await Channels.get_for_send(body["id"])
    assert found is not None and found[1]["heartbeat_url"] == PING_URL
    cleared = await client.patch(
        f"/api/notifications/channels/{body['id']}", json={"heartbeat_url": ""}
    )
    assert cleared.json()["heartbeat_url"] == ""
    found = await Channels.get_for_send(body["id"])
    assert found is not None and "heartbeat_url" not in found[1]
    # The channel's own secrets were never touched.
    assert found[1]["topic"] == "a-b-c-topic-0123456789"


@pytest.mark.asyncio
async def test_a_ping_url_set_by_patch_is_encrypted_and_masked(test_db, client):
    created = await client.post(
        "/api/notifications/channels",
        json={"type": "webhook", "name": "Hook", "config": {"url": "https://hooks.example.org/x"}},
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["id"]
    assert created.json()["heartbeat_url"] == ""

    other = "https://hc-ping.example.org/ffff-patch-token-77"
    for url, token in ((PING_URL, TOKEN), (other, "patch-token-77")):
        response = await client.patch(
            f"/api/notifications/channels/{channel_id}",
            json={"heartbeat_interval_s": 900, "heartbeat_url": url},
        )
        assert response.status_code == 200, response.text
        assert response.json()["heartbeat_url"] == "********"
        assert "heartbeat_url" in response.json()["secret_fields"]
        assert token not in response.text
        raw = await _raw_secrets(test_db, channel_id)
        assert token not in raw  # stored as a v1: token, never in clear
        assert json.loads(raw)["heartbeat_url"].startswith("v1:")
        listed = await client.get("/api/notifications/channels")
        assert token not in listed.text
        found = await Channels.get_for_send(channel_id)
        assert found is not None and found[1]["heartbeat_url"] == url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://check.lan.example/ping/abc",
        "http://127.0.0.1:8000/ping",
        "ftp://hc-ping.example.org/x",
    ],
)
async def test_the_guard_refuses_a_ping_url_on_save(test_db, url):
    with pytest.raises(ChannelError):
        await Channels.create(_webhook(heartbeat_interval_s=300, heartbeat_url=url))
    channel = await Channels.create(_webhook())
    with pytest.raises(ChannelError):
        await Channels.update(
            channel.id, ChannelUpdate(heartbeat_interval_s=300, heartbeat_url=url)
        )


@pytest.mark.asyncio
async def test_ping_mode_gets_the_url_and_never_reads_the_body(test_db, pings):
    channel = await Channels.create(_webhook(heartbeat_interval_s=300, heartbeat_url=PING_URL))
    transport = Recorder()
    dispatcher = _dispatcher(transport, Clock())

    result = await dispatcher.heartbeats.beat(channel.id)
    assert result is not None and result.ok
    [request] = pings.requests
    assert request.method == "GET"
    assert request.headers["host"] == "hc-ping.example.org"
    assert request.url.path == "/0b1c2d3e-ping-token-42"
    assert not pings.body_read
    assert transport.calls == []  # a ping replaces the message
    assert sum(dispatcher._sent.values()) == 1
    assert pings.timeout.read == heartbeat_module.PING_TIMEOUT_S

    await Channels.update(channel.id, ChannelUpdate(heartbeat_method="POST"))
    await dispatcher.heartbeats.beat(channel.id)
    assert pings.requests[-1].method == "POST"


@pytest.mark.asyncio
async def test_ping_refuses_a_redirect(test_db, pings):
    pings.status = 302
    pings.headers = {"location": "http://169.254.169.254/latest/meta-data"}
    channel = await Channels.create(_webhook(heartbeat_interval_s=300, heartbeat_url=PING_URL))
    dispatcher = _dispatcher(Recorder(), Clock())
    result = await dispatcher.heartbeats.beat(channel.id)
    assert result is not None and result.error_code == "redirect_refused"
    assert len(pings.requests) == 1
    assert sum(dispatcher._sent.values()) == 0


@pytest.mark.asyncio
async def test_ping_url_is_validated_again_at_each_send(test_db, pings, monkeypatch):
    channel = await Channels.create(_webhook(heartbeat_interval_s=300, heartbeat_url=PING_URL))
    real = socket.getaddrinfo

    def rebound(host, port, *args, **kwargs):  # DNS now points the host at the LAN
        return real("10.0.0.5", port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", rebound)
    dispatcher = _dispatcher(Recorder(), Clock())
    result = await dispatcher.heartbeats.beat(channel.id)
    assert result is not None and not result.ok
    assert pings.requests == []


@pytest.mark.asyncio
async def test_ping_mode_ignores_the_auto_pause_and_starts_at_once(test_db, pings):
    channel = await Channels.create(_webhook(heartbeat_interval_s=300, heartbeat_url=PING_URL))
    clock = Clock()
    await Channels.set_paused_until(channel.id, int(clock.now) + 3600)
    invalidate_cache()
    sleeper = Sleeper(clock, limit=2)
    dispatcher = _dispatcher(Recorder(), clock, sleeper)
    with pytest.raises(asyncio.CancelledError):
        await dispatcher.heartbeats._loop(channel.id, 300, True)
    # A ping at start (healthchecks starts its period at the first ping), then each interval.
    assert len(pings.requests) == 2
    assert sleeper.calls == [300, 300]


# -- loops ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loops_follow_create_edit_disable_and_delete_without_leaks(test_db, client):
    manager = notification_dispatcher().heartbeats
    await manager.start()
    try:
        created = await client.post(
            "/api/notifications/channels",
            json={
                "type": "webhook",
                "name": "Hook",
                "config": {"url": "https://hooks.example.org/in/abc"},
                "heartbeat_interval_s": 600,
            },
        )
        cid = created.json()["id"]
        assert manager.running == {cid: (600, False)}
        first = manager._beats[cid][0]

        await client.patch(f"/api/notifications/channels/{cid}", json={"name": "Renamed"})
        second = manager._beats[cid][0]
        assert second is not first  # restarted on edit
        await asyncio.sleep(0)
        assert first.cancelled()

        await client.patch(
            f"/api/notifications/channels/{cid}", json={"heartbeat_interval_s": 3600}
        )
        assert manager.running == {cid: (3600, False)}

        await client.patch(f"/api/notifications/channels/{cid}", json={"enabled": False})
        assert manager.running == {}
        await client.patch(f"/api/notifications/channels/{cid}", json={"enabled": True})
        assert manager.running == {cid: (3600, False)}

        last = manager._beats[cid][0]
        await client.delete(f"/api/notifications/channels/{cid}")
        assert manager.running == {}
        await asyncio.sleep(0)
        assert last.cancelled()
    finally:
        await manager.stop()
    assert manager._beats == {}
    leaked = [t for t in asyncio.all_tasks() if t.get_name().startswith("notify-heartbeat:")]
    assert all(t.done() for t in leaked)


# -- backup ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_keeps_the_settings_and_leaves_the_url_out(backup_db):
    channel = await Channels.create(
        _webhook(heartbeat_interval_s=3600, heartbeat_method="POST", heartbeat_url=PING_URL)
    )
    section = await export_notifications()
    assert section is not None
    assert TOKEN not in json.dumps(section.data)
    [item] = [c for c in section.data["channels"] if c["id"] == channel.id]
    assert (item["heartbeat_interval_s"], item["heartbeat_method"]) == (3600, "POST")
    assert item["secret_omitted"]["heartbeat_url"] is True

    # Onto the same channel: the stored URL is kept.
    async with backup_db.tx() as conn:
        assert await restore_notifications(section, conn) is not None
    invalidate_cache()
    found = await Channels.get_for_send(channel.id)
    assert found is not None and found[1]["heartbeat_url"] == PING_URL
    assert found[0].enabled

    # Onto a fresh install: the channel waits for the URL, typed again or cleared.
    await Channels.delete(channel.id)
    async with backup_db.tx() as conn:
        await restore_notifications(section, conn)
    invalidate_cache()
    restored = await Channels.get(channel.id)
    assert restored is not None
    assert (restored.enabled, restored.last_error) == (False, "secret_missing")
    assert (restored.heartbeat_interval_s, restored.heartbeat_method) == (3600, "POST")
    destination = {"url": "https://hooks.example.org/in/abc"}
    with pytest.raises(ChannelError) as refused:
        await Channels.update(channel.id, ChannelUpdate(config=destination, enabled=True))
    assert (refused.value.code, refused.value.params) == (
        "secret_missing",
        {"fields": ["heartbeat_url"]},
    )
    # The ping URL cleared on purpose: the channel beats in message mode.
    done = await Channels.update(
        channel.id, ChannelUpdate(config=destination, heartbeat_url="", enabled=True)
    )
    assert done.enabled and done.last_error is None


@pytest.mark.asyncio
async def test_a_file_without_url_drops_the_stored_one(backup_db):
    channel = await Channels.create(_webhook(heartbeat_interval_s=3600))
    section = await export_notifications()
    assert section is not None
    await Channels.update(channel.id, ChannelUpdate(heartbeat_url=PING_URL))
    async with backup_db.tx() as conn:
        await restore_notifications(section, conn)
    invalidate_cache()
    found = await Channels.get_for_send(channel.id)
    assert found is not None and "heartbeat_url" not in found[1]


@pytest.mark.asyncio
async def test_a_hand_edited_interval_restores_as_off(backup_db):
    channel = await Channels.create(_webhook())
    section = await export_notifications()
    assert section is not None
    for item in section.data["channels"]:
        if item["id"] == channel.id:
            item["heartbeat_interval_s"] = 30
    async with backup_db.tx() as conn:
        warnings = await restore_notifications(section, conn)
    invalidate_cache()
    assert f"notification_heartbeat_dropped:{channel.id}" in warnings
    restored = await Channels.get(channel.id)
    assert restored is not None and restored.heartbeat_interval_s == 0
