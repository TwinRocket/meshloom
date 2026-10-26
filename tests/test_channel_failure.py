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
HASH = "$argon2id$v=19$m=65536,t=2,p=1$YWRtaW5zYWx0$YWRtaW5oYXNo"
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


async def _device(username: str, role: str, endpoint: str) -> int:
    from app.repository.auth import UserRepository
    from app.repository.push_subscriptions import PushSubscriptionRepository

    user = await UserRepository.create(username, role, password_hash=HASH)
    await PushSubscriptionRepository.create(endpoint, "k", "a", label=username, user_id=user.id)
    return user.id


@pytest.mark.asyncio
async def test_three_failures_send_one_notice_to_the_others_once_a_day(test_db):
    await _device("root", "admin", "https://push.example/root")
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


# -- admin-only notices (MSHL-399) -----------------------------------------------------


class Capture:
    """Records each send with the Web Push audience it carried."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    async def send(self, channel, rendered, event, secrets) -> SendResult:
        self.calls.append((channel.id, event.name, event.audience))
        return SendResult("ok")


def _notice() -> dict:
    return {
        "event": "notify.channel_failed",
        "channel_id": "smtp",
        "name": "smtp",
        "error": "auth_failed",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        _notice(),
        {
            "event": "notify.channel_changed",
            "action": "deleted",
            "channel_type": "ntfy",
            "name": "x",
        },
    ],
)
async def test_admin_notices_reach_only_admin_devices_on_web_push(test_db, payload):
    admin = await _device("root", "admin", "https://push.example/root")
    member = await _device("bob", "member", "https://push.example/bob")
    await _add("ntfy-ok")
    transport = Capture()
    dispatcher = _dispatcher(transport, Wall())

    await dispatcher.emit(None, payload=payload, targets=["builtin:push", "ntfy-ok"])
    await dispatcher.wait_idle()

    by_channel = {c: audience for c, _, audience in transport.calls}
    assert set(by_channel) == {"builtin:push", "ntfy-ok"}
    audience = by_channel["builtin:push"]
    assert audience is not None and audience.default is None
    assert audience.payload_for(admin) is not None
    assert audience.payload_for(member) is None
    assert audience.payload_for(None) is None


@pytest.mark.asyncio
async def test_admin_notice_skips_web_push_without_an_admin_device(test_db):
    await _device("bob", "member", "https://push.example/bob")
    await _add("ntfy-ok")
    transport = Capture()
    dispatcher = _dispatcher(transport, Wall())

    await dispatcher.emit(None, payload=_notice(), targets=["builtin:push", "ntfy-ok"])
    await dispatcher.wait_idle()
    assert [c for c, _, _ in transport.calls] == ["ntfy-ok"]


@pytest.mark.asyncio
async def test_admin_notice_fails_closed_when_owners_are_unreadable(test_db, monkeypatch):
    from app.repository.push_subscriptions import PushSubscriptionRepository

    await _device("root", "admin", "https://push.example/root")
    await _add("ntfy-ok")

    async def broken():
        raise RuntimeError("db gone")

    monkeypatch.setattr(PushSubscriptionRepository, "deliverable", staticmethod(broken))
    transport = Capture()
    dispatcher = _dispatcher(transport, Wall())

    await dispatcher.emit(None, payload=_notice(), targets=["builtin:push", "ntfy-ok"])
    await dispatcher.wait_idle()
    assert [c for c, _, _ in transport.calls] == ["ntfy-ok"]


@pytest.mark.asyncio
async def test_admin_notice_is_not_folded_into_a_digest(test_db):
    async with db.tx() as conn:
        await conn.execute(
            "INSERT INTO notification_channels (id, type, name, enabled, config, secrets, "
            "created_at, digest_interval_s) VALUES ('digest', 'webhook', 'digest', 1, '{}', "
            "'{}', 1, 3600)"
        )
    invalidate_cache()
    transport = Capture()
    dispatcher = _dispatcher(transport, Wall())

    await dispatcher.emit(None, payload=_notice(), targets=["digest"])
    await dispatcher.wait_idle()
    assert [c for c, _, _ in transport.calls] == ["digest"]


def _role_push_manager(monkeypatch) -> tuple[object, list[str]]:
    """A PushManager over an admin (1), a member (2) and an ownerless device (3)."""
    from app.push import manager as manager_module
    from app.push.manager import PushManager, _SendResult

    subs = [
        {"id": "a", "user_id": 1, "owner_role": "admin", "label": "", "language": "en"},
        {"id": "m", "user_id": 2, "owner_role": "member", "label": "", "language": "en"},
        {"id": "u", "user_id": 3, "owner_role": None, "label": "", "language": "en"},
    ]

    async def deliverable():
        return subs

    monkeypatch.setattr(
        manager_module.PushSubscriptionRepository, "deliverable", staticmethod(deliverable)
    )
    monkeypatch.setattr(manager_module, "get_vapid_private_key", lambda: "key")
    sent: list[str] = []
    pm = PushManager()

    async def send_one(sub, payload, vapid_key, allow_private=False):
        sent.append(sub["id"])
        return _SendResult(sub["id"], success=True)

    async def no_outcomes(results, labels=None):
        return None

    monkeypatch.setattr(pm, "_send_one", send_one)
    monkeypatch.setattr(pm, "_record_outcomes", no_outcomes)
    monkeypatch.setattr(manager_module.AppSettingsRepository, "get", staticmethod(_raise_settings))
    return pm, sent


@pytest.mark.asyncio
async def test_push_manager_never_sends_an_admin_notice_to_a_member_device(monkeypatch):
    pm, sent = _role_push_manager(monkeypatch)

    await pm.dispatch_event(_notice())
    assert sent == ["a"]
    sent.clear()
    await pm.dispatch_event({"event": "app.update", "version": "9"})
    assert sorted(sent) == ["a", "m", "u"]


async def _raise_settings():
    raise RuntimeError("no settings")


@pytest.mark.asyncio
async def test_an_audience_never_sends_an_admin_notice_to_a_member_device(monkeypatch):
    # The member may have been demoted between the emit and the send: the role is
    # checked again on the device, whatever the audience says.
    from app.notifications.user_rules import PushAudience

    pm, sent = _role_push_manager(monkeypatch)
    payload = _notice()

    await pm.dispatch_event(payload, PushAudience(default=None, per_user={2: payload}))
    assert sent == []
    await pm.dispatch_event(payload, PushAudience(default=payload, per_user={}))
    assert sent == ["a"]


@pytest.mark.asyncio
async def test_a_destination_change_never_reaches_a_member_device(monkeypatch):
    import app.background_tasks as background_tasks
    from app.models import NotificationDestinations
    from app.notify import notify_destination_changed
    from app.push import manager as manager_module

    pm, sent = _role_push_manager(monkeypatch)
    monkeypatch.setattr(manager_module, "push_manager", pm)
    spawned: list = []
    monkeypatch.setattr(background_tasks, "spawn", lambda coro, name=None: spawned.append(coro))
    before = NotificationDestinations()
    after = before.model_copy(
        update={"webhook": before.webhook.model_copy(update={"url": "https://hooks.example/n"})}
    )

    notify_destination_changed(before, after)
    assert len(spawned) == 1
    await spawned[0]
    assert sent == ["a"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result_kwargs", "code"),
    [({"invalid": True, "refused": True}, failures.PUSH_REFUSED), ({}, failures.PUSH_EVICTED)],
)
async def test_evicted_and_refused_browsers_are_announced(
    test_db, monkeypatch, result_kwargs, code
):
    import app.notifications.dispatcher as dispatcher_module
    from app.push.manager import _SendResult, push_manager
    from app.repository.push_subscriptions import (
        MAX_CONSECUTIVE_FAILURES,
        PushSubscriptionRepository,
    )

    emitted: list[dict] = []

    async def capture(event, *, payload=None, targets=None, **_kwargs):
        emitted.append(payload)
        return len(targets or [])

    monkeypatch.setattr(dispatcher_module, "emit", capture)
    await PushSubscriptionRepository.create("https://push.example/a", "k", "a")
    sub_id = (await PushSubscriptionRepository.get_all())[0]["id"]
    if code == failures.PUSH_EVICTED:
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE push_subscriptions SET failure_count = ?",
                (MAX_CONSECUTIVE_FAILURES - 1,),
            )

    await push_manager._record_outcomes([_SendResult(sub_id, **result_kwargs)], {sub_id: "Pixel"})
    assert await PushSubscriptionRepository.get_all() == []
    assert emitted == [
        {
            "event": "notify.channel_failed",
            "channel_id": "builtin:push",
            "name": "Pixel",
            "error": code,
        }
    ]


_REMOVED_TEXTS = {
    ("push_unsubscribed", "en"): ("Browser unsubscribed", "unsubscribed by its push service"),
    ("push_unsubscribed", "fr"): ("Navigateur désabonné", "désabonné par son service push"),
    ("push_evicted", "en"): ("Browser removed", "failed 15 pushes in a row and was removed"),
    ("push_evicted", "fr"): ("Navigateur retiré", "a échoué 15 envois de suite et a été retiré"),
    ("push_refused", "en"): ("Browser removed", "refused by the network guard"),
    ("push_refused", "fr"): ("Navigateur retiré", "refusée par la protection réseau"),
}


@pytest.mark.parametrize("code", ["push_unsubscribed", "push_evicted", "push_refused"])
@pytest.mark.parametrize("lang", ["fr", "en"])
def test_removed_browser_notices_are_rendered_in_both_languages(code, lang):
    from app.notifications.render import build_push_payload

    rendered = json.loads(
        build_push_payload(
            {
                "event": "notify.channel_failed",
                "channel_id": "builtin:push",
                "name": "Pixel",
                "error": code,
            },
            lang,
        )
    )
    assert "Pixel" in rendered["body"]
    assert code not in rendered["body"]
    title, fragment = _REMOVED_TEXTS[(code, lang)]
    assert rendered["title"] == title
    assert fragment in rendered["body"]


# -- test and heartbeat audiences on Web Push (MSHL-407) ---------------------------------


async def _push_channel():
    found = await Channels.get_for_send("builtin:push")
    assert found is not None
    return found


@pytest.mark.asyncio
async def test_a_push_test_reaches_only_the_requester(test_db):
    admin = await _device("root", "admin", "https://push.example/root")
    member = await _device("bob", "member", "https://push.example/bob")
    other = await _device("eve", "member", "https://push.example/eve")
    transport = Capture()
    dispatcher = _dispatcher(transport, Wall())
    channel, secrets = await _push_channel()

    for requester in (member, admin):
        transport.calls.clear()
        result = await dispatcher.send_test(channel, secrets, requester_id=requester)
        assert result.ok
        [(_, name, audience)] = transport.calls
        assert name == "notify.test"
        assert audience.default is None
        assert audience.payload_for(requester) is not None
        assert all(
            audience.payload_for(uid) is None
            for uid in (admin, member, other, None)
            if uid != requester
        )


@pytest.mark.asyncio
async def test_a_push_probe_reaches_only_admins_and_fails_closed(test_db, monkeypatch):
    from app.repository.push_subscriptions import PushSubscriptionRepository

    admin = await _device("root", "admin", "https://push.example/root")
    member = await _device("bob", "member", "https://push.example/bob")
    transport = Capture()
    dispatcher = _dispatcher(transport, Wall())
    channel, secrets = await _push_channel()

    assert (await dispatcher.send_test(channel, secrets)).ok
    [(_, _, audience)] = transport.calls
    assert audience.payload_for(admin) is not None
    assert audience.payload_for(member) is None and audience.payload_for(None) is None

    async def broken():
        raise RuntimeError("db gone")

    monkeypatch.setattr(PushSubscriptionRepository, "deliverable", staticmethod(broken))
    transport.calls.clear()
    assert (await dispatcher.send_test(channel, secrets)).ok
    assert transport.calls == []  # nothing sent to anyone


@pytest.mark.asyncio
async def test_a_test_on_another_channel_carries_no_audience(test_db):
    await _add("ntfy-ok")
    transport = Capture()
    dispatcher = _dispatcher(transport, Wall())
    channel, secrets = await Channels.get_for_send("ntfy-ok")

    assert (await dispatcher.send_test(channel, secrets, requester_id=7)).ok
    assert transport.calls == [("ntfy-ok", "notify.test", None)]


@pytest.mark.asyncio
async def test_the_push_test_route_targets_the_caller(test_db, client, monkeypatch):
    from app.push import manager as manager_module

    seen: list = []

    async def dispatch_event(data, audience=None):
        seen.append((data.get("event"), audience))
        return manager_module.PushOutcome(total=1, delivered=1)

    monkeypatch.setattr(manager_module.push_manager, "dispatch_event", dispatch_event)
    response = await client.post("/api/notifications/channels/builtin:push/test")
    assert response.status_code == 200, response.text
    [(_, audience)] = seen  # the test payload (``notify.TEST_ALERT_PAYLOAD``)
    assert set(audience.per_user) == {1} and audience.default is None  # the signed-in admin


@pytest.mark.asyncio
async def test_a_heartbeat_reaches_only_admin_devices_on_web_push(test_db, monkeypatch):
    from app.notifications.render import HEARTBEAT_EVENT

    payload = {"event": HEARTBEAT_EVENT, "instance": "x", "uptime_s": 5, "version": "5.0.0"}
    pm, sent = _role_push_manager(monkeypatch)
    await pm.dispatch_event(payload)
    assert sent == ["a"]

    admin = await _device("root", "admin", "https://push.example/root")
    member = await _device("bob", "member", "https://push.example/bob")
    await _add("ntfy-ok")
    transport = Capture()
    dispatcher = _dispatcher(transport, Wall())
    await dispatcher.emit(None, payload=payload, targets=["builtin:push", "ntfy-ok"])
    await dispatcher.wait_idle()
    by_channel = {c: audience for c, _, audience in transport.calls}
    audience = by_channel["builtin:push"]
    assert audience.payload_for(admin) is not None and audience.payload_for(member) is None


@pytest.mark.asyncio
async def test_a_heartbeat_skips_web_push_when_owners_are_unreadable(test_db, monkeypatch):
    from app.notifications.render import HEARTBEAT_EVENT
    from app.repository.push_subscriptions import PushSubscriptionRepository

    await _device("root", "admin", "https://push.example/root")
    await _add("ntfy-ok")

    async def broken():
        raise RuntimeError("db gone")

    monkeypatch.setattr(PushSubscriptionRepository, "deliverable", staticmethod(broken))
    transport = Capture()
    dispatcher = _dispatcher(transport, Wall())
    payload = {"event": HEARTBEAT_EVENT, "instance": "x", "uptime_s": 5, "version": "5.0.0"}
    await dispatcher.emit(None, payload=payload, targets=["builtin:push", "ntfy-ok"])
    await dispatcher.wait_idle()
    assert [c for c, _, _ in transport.calls] == ["ntfy-ok"]


def test_channel_failed_is_urgent():
    from app.notifications.events import event_from_payload

    event = event_from_payload(_notice())
    assert event is not None and event.urgent
