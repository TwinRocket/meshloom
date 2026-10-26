"""A channel that keeps failing (MSHL-130): the ``notify.channel_failed`` notice to the other
channels (once a day, across restarts), the auto-pause and its backoff, the probe, the Resume
route, the health flag and the notice for a browser its push service unsubscribed."""

from __future__ import annotations

import json

import pytest

from app.notifications import failures
from app.notifications.dispatcher import Dispatcher
from app.notifications.transports.base import SendResult
from app.repository.notification_channels import (
    NotificationChannelRepository as Channels,
)
from app.repository.notification_channels import (
    NotificationJournalRepository as Journal,
)
from app.repository.notification_channels import invalidate_cache

pytestmark = pytest.mark.async_notifications

T0 = 1_800_000_000
db = None


class Wall:
    def __init__(self) -> None:
        self.now = float(T0)

    def __call__(self) -> float:
        return self.now


class Script:
    """``bad`` channels answer ``failed`` (or ``answer`` for them), the others ``ok``."""

    def __init__(self, bad: set[str]) -> None:
        self.bad = bad
        self.answer = SendResult("failed", error_code="auth_failed")
        self.calls: list[tuple[str, str, dict]] = []

    async def send(self, channel, rendered, event, secrets) -> SendResult:
        self.calls.append((channel.id, event.name, dict(event.payload)))
        return self.answer if channel.id in self.bad else SendResult("ok")

    def notices(self) -> list[tuple[str, dict]]:
        return [(c, p) for c, n, p in self.calls if n == "notify.channel_failed"]


@pytest.fixture(autouse=True)
def _wired(request):
    global db
    invalidate_cache()
    db = request.getfixturevalue("test_db") if "test_db" in request.fixturenames else None
    yield
    invalidate_cache()


async def _add(channel_id: str, *, secrets: dict | None = None, **cols) -> None:
    async with db.tx() as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO notification_channels (id, type, name, enabled, config, "
            "secrets, created_at, paused_until, failure_count) "
            "VALUES (?, 'webhook', ?, ?, '{}', ?, 1, ?, ?)",
            (
                channel_id,
                channel_id,
                cols.get("enabled", 1),
                json.dumps(secrets or {}),
                cols.get("paused_until"),
                cols.get("failure_count", 0),
            ),
        )
    invalidate_cache()


def _dispatcher(transport, wall: Wall) -> Dispatcher:
    async def no_sleep(_seconds: float) -> None:
        return None

    return Dispatcher(
        clock=wall, sleep=no_sleep, wall_clock=wall, transport_for=lambda _c: transport
    )


async def _fail(dispatcher: Dispatcher, channel_id: str, times: int, code="auth_failed") -> None:
    for _ in range(times):
        channel = await Channels.get(channel_id)
        await dispatcher._record_failure(channel, code)
    await dispatcher.wait_idle()


def _app_update(n: int) -> dict:
    return {"event": "oss_update", "current": "1.0.0", "latest": f"9.9.{n}"}


# -- the notice ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_three_failures_send_one_notice_to_the_others_once_a_day(test_db):
    await _add("smtp-bad", secrets={"password": "hunter2-secret"})
    await _add("ntfy-ok")
    await _add("off", enabled=0)
    transport = Script({"smtp-bad"})
    transport.answer = SendResult(
        "failed", error_code="auth_failed https://bot123:hunter2-secret@h.example/x"
    )
    wall = Wall()
    dispatcher = _dispatcher(transport, wall)

    for n in range(2):
        await dispatcher.emit(None, payload=_app_update(n), targets=["smtp-bad"])
        await dispatcher.wait_idle()
    assert transport.notices() == []

    await dispatcher.emit(None, payload=_app_update(2), targets=["smtp-bad"])
    await dispatcher.wait_idle()
    notices = transport.notices()
    # Every other enabled channel, Web Push included; never the failing or a disabled one.
    assert sorted(c for c, _ in notices) == ["builtin:push", "ntfy-ok"]
    payload = notices[0][1]
    assert payload["channel_id"] == "smtp-bad" and payload["name"] == "smtp-bad"
    # A code, never the exception text, a URL or a secret.
    assert payload["error"] == "send_failed"
    assert "hunter2" not in json.dumps(transport.calls)

    # More failures the same day: no new notice.
    for n in range(3, 6):
        await dispatcher.emit(None, payload=_app_update(n), targets=["smtp-bad"])
        await dispatcher.wait_idle()
    assert len(transport.notices()) == 2

    # A restart (a new dispatcher) the same day: still none.
    restarted = _dispatcher(transport, wall)
    await _fail(restarted, "smtp-bad", 1)
    assert len(transport.notices()) == 2

    # The next day: one more.
    wall.now += failures.ANNOUNCE_TTL_S + 1
    await _fail(restarted, "smtp-bad", 1)
    assert len(transport.notices()) == 4


def test_the_notice_error_is_a_code_never_a_secret():
    assert failures.safe_error_code("auth_failed") == "auth_failed"
    assert failures.safe_error_code("http_500") == "http_500"
    assert failures.safe_error_code("line 2 (tgram): http_401") == "line 2: http_401"
    for leaky in (
        "https://user:pa55@hooks.example/x",
        "tgram://123456:ABCdef/123",
        "smtp auth failed for bob: s3cret",
        "http_403 ****",
        None,
        "",
    ):
        assert failures.safe_error_code(leaky) == "send_failed"


# -- the auto-pause -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_twenty_failures_pause_the_channel_and_hold_even_urgent_events(test_db):
    await _add("tg-bad")
    transport = Script({"tg-bad"})
    wall = Wall()
    dispatcher = _dispatcher(transport, wall)

    await _fail(dispatcher, "tg-bad", failures.PAUSE_AFTER - 1)
    assert (await Channels.get("tg-bad")).paused_until is None

    await _fail(dispatcher, "tg-bad", 1)
    channel = await Channels.get("tg-bad")
    assert channel.paused_until == T0 + 300
    assert (await dispatcher.statuses())["tg-bad"]["status"] == "paused"

    # More failures while paused (jobs already queued) do not move the step.
    await _fail(dispatcher, "tg-bad", 3)
    assert (await Channels.get("tg-bad")).paused_until == T0 + 300

    # A paused channel gets nothing, urgent events included.
    queued = await dispatcher.emit(
        None,
        payload={"event": "radio_offline", "offline_s": 600, "outage_started_at": T0},
        targets=["tg-bad"],
    )
    assert queued == 0


@pytest.mark.asyncio
async def test_the_probe_backs_off_5_min_15_min_1_h_then_6_h_and_a_success_resumes(test_db):
    await _add("tg-bad")
    transport = Script({"tg-bad"})
    wall = Wall()
    dispatcher = _dispatcher(transport, wall)
    await _fail(dispatcher, "tg-bad", failures.PAUSE_AFTER)

    pauses = [(await Channels.get("tg-bad")).paused_until - int(wall.now)]
    for _ in range(4):
        assert await dispatcher.probe_tick() == 0  # the pause still holds
        wall.now = (await Channels.get("tg-bad")).paused_until
        assert await dispatcher.probe_tick() == 1
        pauses.append((await Channels.get("tg-bad")).paused_until - int(wall.now))
    assert pauses == [300, 900, 3600, 21600, 21600]
    assert [n for _c, n, _p in transport.calls].count("notify.test") == 4

    transport.bad.clear()
    wall.now = (await Channels.get("tg-bad")).paused_until
    assert await dispatcher.probe_tick() == 1
    channel = await Channels.get("tg-bad")
    assert channel.paused_until is None and channel.failure_count == 0
    assert await Journal.state_get(failures.autopause_key("tg-bad")) is None
    assert (await dispatcher.statuses())["tg-bad"]["status"] == "connected"


@pytest.mark.asyncio
async def test_deleting_a_channel_drops_its_autopause_step(test_db):
    await _add("tg-bad")
    await _add("tg-other")
    dispatcher = _dispatcher(Script({"tg-bad", "tg-other"}), Wall())
    await _fail(dispatcher, "tg-bad", failures.PAUSE_AFTER)
    await _fail(dispatcher, "tg-other", failures.PAUSE_AFTER)
    assert await Journal.state_get(failures.autopause_key("tg-bad")) is not None

    await Channels.delete("tg-bad")
    assert await Journal.state_get(failures.autopause_key("tg-bad")) is None
    assert await Journal.state_get(failures.autopause_key("tg-other")) is not None


@pytest.mark.asyncio
async def test_the_probe_skips_channels_it_did_not_pause(test_db):
    await _add("manual", paused_until=T0 - 10)
    await _add("off", enabled=0, paused_until=T0 - 10)
    dispatcher = _dispatcher(Script(set()), Wall())
    assert await dispatcher.probe_tick() == 0


# -- Resume and health --------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_route_clears_the_pause_and_the_failures(test_db, client):
    await _add("tg-bad", paused_until=T0 + 10**9, failure_count=25)
    await Journal.state_set(failures.autopause_key("tg-bad"), "2")
    assert (await client.get("/api/health")).json()["notifications_failing"] is True

    response = await client.post("/api/notifications/channels/tg-bad/resume")
    assert response.status_code == 200
    body = response.json()
    assert body["paused_until"] is None and body["failure_count"] == 0
    assert await Journal.state_get(failures.autopause_key("tg-bad")) is None
    assert (await client.get("/api/health")).json()["notifications_failing"] is False

    missing = await client.post("/api/notifications/channels/nope-1/resume")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_resume_is_admin_only(test_db, client, sign_in_as):
    await _add("tg-bad", paused_until=T0 + 10**9, failure_count=25)
    for role in ("member", "viewer", "kiosk"):
        sign_in_as(role)
        response = await client.post("/api/notifications/channels/tg-bad/resume")
        assert response.status_code == 403, role
    assert (await Channels.get("tg-bad")).paused_until == T0 + 10**9


@pytest.mark.asyncio
async def test_health_flags_failing_channels_from_the_third_failure(test_db):
    await _add("tg-bad")
    dispatcher = _dispatcher(Script({"tg-bad"}), Wall())
    assert await dispatcher.failing() is False
    await _fail(dispatcher, "tg-bad", failures.FAILED_EVENT_AFTER - 1)
    assert await dispatcher.failing() is False
    await _fail(dispatcher, "tg-bad", 1)
    assert await dispatcher.failing() is True


# -- Web Push ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unsubscribed_browser_is_announced_once(test_db, monkeypatch):
    import app.notifications.dispatcher as dispatcher_module
    from app.push.manager import _SendResult, push_manager
    from app.repository.push_subscriptions import PushSubscriptionRepository

    await _add("ntfy-ok")
    emitted: list[tuple[dict, list[str]]] = []

    async def capture(event, *, payload=None, targets=None, **_kwargs):
        emitted.append((payload, list(targets or [])))
        return len(targets or [])

    monkeypatch.setattr(dispatcher_module, "emit", capture)
    await PushSubscriptionRepository.create("https://push.example/a", "k", "a")
    sub_id = (await PushSubscriptionRepository.get_all())[0]["id"]

    await push_manager._record_outcomes([_SendResult(sub_id, expired=True)], {sub_id: "Pixel"})
    assert await PushSubscriptionRepository.get_all() == []
    assert len(emitted) == 1
    payload, targets = emitted[0]
    assert payload == {
        "event": "notify.channel_failed",
        "channel_id": "builtin:push",
        "name": "Pixel",
        "error": failures.PUSH_UNSUBSCRIBED,
    }
    # The other browsers still work: Web Push is a target too.
    assert targets == ["builtin:push", "ntfy-ok"]

    await push_manager._record_outcomes([_SendResult(sub_id, expired=True)], {sub_id: "Pixel"})
    assert len(emitted) == 1
