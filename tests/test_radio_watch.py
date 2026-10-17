"""Radio offline / online / app started notifications (MSHL-105), on a fake clock."""

from dataclasses import dataclass

import pytest

from app.notifications.radio_watch import RadioWatch
from app.push.manager import _build_payload, event_notification_text, format_duration


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __call__(self) -> float:
        return self.now


@dataclass
class FakeRadio:
    is_connected: bool = True
    is_setup_complete: bool = True
    connection_desired: bool = True
    connection_info: str | None = "Serial: /dev/ttyUSB0"
    rebooting: bool = False


class MemoryStore:
    def __init__(self, started_at: float | None = None) -> None:
        self.started_at = started_at

    async def load(self) -> float | None:
        return self.started_at

    async def save(self, started_at: float | None) -> None:
        self.started_at = started_at


class Harness:
    def __init__(self, grace: int = 60, store: MemoryStore | None = None) -> None:
        self.clock = FakeClock()
        self.events: list[tuple[str, dict]] = []
        self.grace = grace
        self.store = store or MemoryStore()
        self.radio = FakeRadio()
        self.watch = self.new_watch()

    def new_watch(self) -> RadioWatch:
        async def get_grace() -> int:
            return self.grace

        return RadioWatch(
            emit=lambda event, payload: self.events.append((event, payload)),
            store=self.store,
            get_grace=get_grace,
            is_excused=lambda radio: radio.rebooting,
            monotonic=self.clock,
            wall=self.clock,
        )

    async def tick(self, seconds: float = 5.0) -> None:
        self.clock.advance(seconds)
        await self.watch.observe(self.radio)

    async def run(self, seconds: float, step: float = 5.0) -> None:
        end = self.clock.now + seconds
        while self.clock.now < end:
            await self.tick(min(step, end - self.clock.now))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    async def up_and_armed(self) -> None:
        """Radio set up, and the startup silence window over."""
        await self.tick()
        await self.clock_past_startup()

    async def clock_past_startup(self) -> None:
        await self.run(self.grace + 5)

    def drop(self) -> None:
        self.radio.is_connected = False
        self.radio.is_setup_complete = False

    def restore(self) -> None:
        self.radio.is_connected = True
        self.radio.is_setup_complete = True


@pytest.mark.asyncio
async def test_loss_recovered_within_grace_sends_nothing():
    h = Harness()
    await h.up_and_armed()
    h.drop()
    await h.run(40)
    h.restore()
    await h.run(120)
    assert h.events == []
    assert h.store.started_at is None


@pytest.mark.asyncio
async def test_loss_longer_than_grace_sends_one_offline_then_one_online_with_downtime():
    h = Harness()
    await h.up_and_armed()
    h.drop()
    lost_at = h.clock.now
    await h.run(55)
    assert h.events == []
    await h.run(300)
    assert h.names() == ["radio_offline"]  # once per outage, not once per pass
    name, payload = h.events[0]
    assert payload["urgent"] is True
    assert payload["connection"] == "Serial: /dev/ttyUSB0"
    assert payload["offline_s"] >= 60
    # Persisted at notification time, with the real start of the outage.
    assert h.store.started_at is not None
    assert lost_at <= h.store.started_at <= lost_at + 5

    h.restore()
    await h.run(300)
    assert h.names() == ["radio_offline", "radio_online"]
    downtime = h.events[1][1]["downtime_s"]
    assert 350 <= downtime <= 360
    assert h.store.started_at is None


@pytest.mark.asyncio
async def test_grace_zero_notifies_at_once():
    h = Harness(grace=0)
    await h.tick()
    h.drop()
    await h.tick()
    assert h.names() == ["radio_offline"]


@pytest.mark.asyncio
async def test_expected_reboot_sends_nothing():
    h = Harness()
    await h.up_and_armed()
    h.drop()
    h.radio.rebooting = True
    await h.run(600)
    assert h.events == []


@pytest.mark.asyncio
async def test_pending_loss_is_cancelled_by_an_expected_reboot():
    h = Harness()
    await h.up_and_armed()
    h.drop()
    await h.run(30)
    h.radio.rebooting = True
    await h.run(30)
    h.radio.rebooting = False  # the reboot window is over, still down: a new loss
    await h.run(30)
    assert h.events == []
    await h.run(40)
    assert h.names() == ["radio_offline"]


@pytest.mark.asyncio
async def test_operator_pause_sends_nothing_and_closes_a_notified_outage_silently():
    h = Harness()
    await h.up_and_armed()
    h.drop()
    h.radio.connection_desired = False
    await h.run(600)
    assert h.events == []

    h.radio.connection_desired = True
    await h.run(120)
    assert h.names() == ["radio_offline"]
    h.radio.connection_desired = False
    await h.run(30)
    assert h.store.started_at is None
    h.radio.connection_desired = True
    h.restore()
    await h.run(30)
    assert h.names() == ["radio_offline"]  # no radio_online for a paused outage


@pytest.mark.asyncio
async def test_two_losses_are_two_outages():
    h = Harness()
    await h.up_and_armed()
    for _ in range(2):
        h.drop()
        await h.run(90)
        h.restore()
        await h.run(30)
    assert h.names() == ["radio_offline", "radio_online"] * 2


@pytest.mark.asyncio
async def test_restart_during_an_outage_keeps_the_start_time():
    h = Harness()
    await h.up_and_armed()
    h.drop()
    await h.run(90)
    assert h.names() == ["radio_offline"]
    started_at = h.store.started_at

    # Meshloom restarts 10 minutes into the outage: a new watch, same database.
    await h.run(600)
    h.events.clear()
    h.watch = h.new_watch()
    await h.run(30)
    assert h.events == []  # still down: no second radio_offline
    h.restore()
    await h.run(30)
    assert h.names() == ["radio_online"]
    assert h.events[0][1]["downtime_s"] >= h.clock.now - started_at - 40
    assert h.events[0][1]["downtime_s"] > 600


@pytest.mark.asyncio
async def test_no_offline_before_the_first_setup_since_start():
    h = Harness()
    h.drop()  # never came up since the process started
    await h.run(600)
    assert h.events == []


@pytest.mark.asyncio
async def test_startup_silence_holds_the_alert_until_the_grace_has_elapsed_since_start():
    h = Harness(grace=60)
    await h.tick()  # first setup done
    h.drop()  # an update restart: the radio is slow to come back
    await h.run(50)  # 55 s after start
    assert h.events == []
    h.restore()
    await h.run(30)
    assert h.events == []


@pytest.mark.asyncio
async def test_connected_radio_still_in_setup_is_not_lost():
    h = Harness()
    await h.up_and_armed()
    h.radio.is_setup_complete = False  # reconnected, long offload
    await h.run(300)
    assert h.events == []


@pytest.mark.asyncio
async def test_app_started_is_sent_once():
    h = Harness()
    h.watch.note_startup_complete(version="5.0.0", radio_connected=True, connect_s=12.34)
    h.watch.note_startup_complete(version="5.0.0", radio_connected=True, connect_s=1.0)
    assert h.events == [
        ("app_started", {"version": "5.0.0", "radio_connected": True, "connect_s": 12.3})
    ]


@pytest.mark.asyncio
async def test_a_failing_store_never_breaks_the_monitor():
    class Broken(MemoryStore):
        async def save(self, started_at):
            raise RuntimeError("disk full")

    h = Harness(grace=0, store=Broken())
    await h.tick()
    h.drop()
    await h.tick()
    assert h.names() == ["radio_offline"]


def test_format_duration():
    assert [format_duration(s) for s in (0, 45, 60, 119, 720, 3600, 7500, "x", None)] == [
        "0 s",
        "45 s",
        "1 min",
        "1 min",
        "12 min",
        "1 h 00",
        "2 h 05",
        "0 s",
        "0 s",
    ]


def test_rendering_fr_en():
    offline = {"event": "radio_offline", "offline_s": 60, "connection": "Serial: /dev/ttyUSB0"}
    assert (
        event_notification_text(offline, "fr")[0]
        == "Radio hors ligne depuis 1 min (USB /dev/ttyUSB0)"
    )
    assert event_notification_text(offline, "en")[0] == "Radio offline for 1 min (USB /dev/ttyUSB0)"
    online = {"event": "radio_online", "downtime_s": 720}
    assert event_notification_text(online, "en")[0] == "Radio back online after 12 min"
    assert event_notification_text(online, "fr")[0] == "Radio de nouveau en ligne après 12 min"
    started = {"event": "app_started", "version": "5.0.0", "radio_connected": True, "connect_s": 12}
    assert event_notification_text(started, "fr")[0] == "Meshloom a démarré (5.0.0)"
    assert event_notification_text(started, "fr")[1] == "Radio connectée en 12 s"
    assert event_notification_text(started, "en")[1] == "Radio connected in 12 s"
    assert '"url_hash": "#settings/radio"' in _build_payload(started, "en")


def test_urgent_email_subject_prefix():
    from app.email_template import email_subject

    payload = {"event": "radio_offline", "offline_s": 60, "urgent": True}
    assert email_subject(payload, "en").startswith("[URGENT] Radio offline")
    assert not email_subject({"event": "radio_online", "downtime_s": 5}, "en").startswith("[")


@pytest.mark.asyncio
async def test_db_store_round_trip_and_default_grace(test_db):
    from app.notifications.radio_watch import DbOutageStore
    from app.repository.settings import AppSettingsRepository

    store = DbOutageStore()
    assert await store.load() is None
    await store.save(1234.5)
    assert await store.load() == 1234.5
    await store.save(None)
    assert await store.load() is None

    assert (await AppSettingsRepository.get()).radio_offline_grace_s == 60
    updated = await AppSettingsRepository.update(radio_offline_grace_s=0)
    assert updated.radio_offline_grace_s == 0
    updated = await AppSettingsRepository.update(radio_offline_grace_s=99999)
    assert updated.radio_offline_grace_s == 3600


@pytest.mark.asyncio
async def test_dispatch_uses_the_push_defaults_of_the_event(test_db, monkeypatch):
    from app.notifications import radio_watch as module

    sent: list[tuple[dict, object]] = []

    async def fake_dispatch(payload, channels):
        sent.append((payload, channels))

    monkeypatch.setattr("app.notify.dispatch_system_event", fake_dispatch)
    await module._dispatch("radio_offline", {"urgent": True, "offline_s": 61})
    assert sent[0][0]["event"] == "radio_offline"
    assert sent[0][1]["push"] is True  # push on by default, no migration
    assert sent[0][1]["email"] is False


class _StartupRadio:
    def __init__(self, desired=True, connects=True, error=None):
        self.connection_desired = desired
        self._connects = connects
        self._error = error

    async def reconnect_and_prepare(self, broadcast_on_success=True):
        if self._error:
            raise self._error
        return self._connects


@pytest.mark.parametrize(
    ("radio", "connected"),
    [
        (_StartupRadio(connects=True), True),
        (_StartupRadio(connects=False), False),
        (_StartupRadio(desired=False), False),
        (_StartupRadio(error=RuntimeError("boom")), False),
    ],
)
@pytest.mark.asyncio
async def test_startup_task_sends_app_started_once(radio, connected, monkeypatch, test_db):
    import app.main as main
    from app.notifications.radio_watch import radio_watch

    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(radio_watch, "_emit", lambda event, payload: sent.append((event, payload)))
    radio_watch.reset()
    monkeypatch.setattr(main, "radio_manager", radio)

    async def configured():
        from types import SimpleNamespace

        return SimpleNamespace(configured=True)

    monkeypatch.setattr("app.services.radio_transport.get_transport", configured)
    await main._startup_radio_connect_and_setup()
    assert [name for name, _ in sent] == ["app_started"]
    assert sent[0][1]["radio_connected"] is connected
    assert (sent[0][1]["connect_s"] is not None) is connected
    assert sent[0][1]["version"]


@pytest.mark.asyncio
async def test_startup_task_cancelled_sends_nothing(monkeypatch, test_db):
    import asyncio

    import app.main as main
    from app.notifications.radio_watch import radio_watch

    sent: list[str] = []
    monkeypatch.setattr(radio_watch, "_emit", lambda event, payload: sent.append(event))
    radio_watch.reset()
    monkeypatch.setattr(main, "radio_manager", _StartupRadio(error=asyncio.CancelledError()))

    async def configured():
        from types import SimpleNamespace

        return SimpleNamespace(configured=True)

    monkeypatch.setattr("app.services.radio_transport.get_transport", configured)
    with pytest.raises(asyncio.CancelledError):
        await main._startup_radio_connect_and_setup()
    assert sent == []
