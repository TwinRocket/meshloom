"""Web Push for keyword rules and watched nodes (MSHL-400): administrators' devices only, fail
closed, an account's own pause and conversation exception respected, no second push for an
administrator who already gets the message, no message text on a device whose owner may not
read it, payloads clipped, and the 15-failure eviction still applies."""

from __future__ import annotations

import json

import pytest

from app.notifications import events as ev
from app.notifications import failures
from app.notifications.dispatcher import Dispatcher
from app.notifications.render import PUSH_BODY_MAX, PUSH_TITLE_MAX, build_push_payload
from app.notifications.transports.base import SendResult
from app.notifications.user_rules import (
    PushAudience,
    admin_push_payload,
    instance_rule_audience,
    may_read_message,
    redact_message,
)
from app.repository.notification_channels import invalidate_cache

pytestmark = pytest.mark.async_notifications

T0 = 1_800_000_000
HASH = "$argon2id$v=19$m=65536,t=2,p=1$YWRtaW5zYWx0$YWRtaW5oYXNo"
KEY = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"
NODE = "ab" * 32


@pytest.fixture(autouse=True)
def _fresh_cache():
    invalidate_cache()
    yield
    invalidate_cache()


def _keyword(**extra) -> dict:
    return {
        "type": "CHAN",
        "conversation_key": KEY,
        "channel_name": "#paris",
        "sender_name": "Léa",
        "text": "Léa: secret secours",
        "id": 7,
        "sender_timestamp": 1,
        "keyword": "secours",
        "keyword_id": "r1",
    } | extra


def _node(event: str = "node_seen") -> dict:
    return {
        "event": event,
        "public_key": NODE,
        "name": "Relay",
        "contact_type": 2,
        "silent_s": 0,
        "heard_at": T0,
    }


class Wall:
    def __call__(self) -> float:
        return float(T0)


class Capture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    async def send(self, channel, rendered, event, secrets) -> SendResult:
        self.calls.append((channel.id, event.name, event.audience))
        return SendResult("ok")


def _dispatcher(transport) -> Dispatcher:
    async def no_sleep(_seconds: float) -> None:
        return None

    return Dispatcher(
        clock=Wall(), sleep=no_sleep, wall_clock=Wall(), transport_for=lambda _c: transport
    )


async def _device(username: str, role: str, endpoint: str) -> int:
    from app.repository.auth import UserRepository
    from app.repository.push_subscriptions import PushSubscriptionRepository

    user = await UserRepository.create(username, role, password_hash=HASH)
    await PushSubscriptionRepository.create(endpoint, "k", "a", label=username, user_id=user.id)
    return user.id


# -- the audience ----------------------------------------------------------------------


def test_the_audience_is_the_administrators_minus_their_own_choices():
    event = ev.event_from_payload(_keyword())
    assert event is not None and event.event == "message.keyword"
    payload = _keyword()
    overrides = {
        2: {"pause": {"until": None}},
        3: {"conversations": {f"channel-{KEY}": False}},
    }
    audience = instance_rule_audience([1, 2, 3, 4], overrides, event, payload, now=T0)
    assert audience is not None and audience.default is None
    assert set(audience.per_user) == {1, 4}
    # An administrator the message itself already reaches gets no second push.
    already = PushAudience(default=None, per_user={4: payload})
    audience = instance_rule_audience([1, 4], {}, event, payload, already=already, now=T0)
    assert audience is not None and set(audience.per_user) == {1}
    assert instance_rule_audience([], {}, event, payload) is None
    assert instance_rule_audience([4], {}, event, payload, already=already) is None


def test_the_device_side_check_knows_the_instance_rule_payloads():
    assert admin_push_payload(_keyword())
    assert admin_push_payload(_keyword(event="message"))
    assert admin_push_payload(_node("node_seen")) and admin_push_payload(_node("node_back"))
    assert not admin_push_payload(_keyword(keyword="", keyword_id=""))
    assert not admin_push_payload({"event": "oss_update"})


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [_keyword(), _node("node_seen"), _node("node_back")])
async def test_instance_rule_pushes_reach_only_admin_devices(test_db, payload):
    admin = await _device("root", "admin", "https://push.example/root")
    member = await _device("bob", "member", "https://push.example/bob")
    viewer = await _device("vic", "viewer", "https://push.example/vic")
    transport = Capture()
    dispatcher = _dispatcher(transport)

    await dispatcher.emit(None, payload=payload, targets=["builtin:push"])
    await dispatcher.wait_idle()

    [(channel, _name, audience)] = transport.calls
    assert channel == "builtin:push"
    assert isinstance(audience, PushAudience) and audience.default is None
    assert audience.payload_for(admin) is not None
    for uid in (member, viewer, None):
        assert audience.payload_for(uid) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [_keyword(), _node()])
async def test_no_admin_device_means_no_web_push(test_db, payload):
    from app.repository.notification_channels import NotificationChannelRepository

    await _device("bob", "member", "https://push.example/bob")
    async with test_db.tx() as conn:
        await conn.execute(
            "INSERT INTO notification_channels (id, type, name, enabled, config, secrets, "
            "created_at) VALUES ('ntfy-ok', 'webhook', 'ntfy-ok', 1, '{}', '{}', 1)"
        )
    invalidate_cache()
    assert await NotificationChannelRepository.get("ntfy-ok") is not None
    transport = Capture()
    dispatcher = _dispatcher(transport)

    await dispatcher.emit(None, payload=payload, targets=["builtin:push", "ntfy-ok"])
    await dispatcher.wait_idle()
    assert [c for c, _, _ in transport.calls] == ["ntfy-ok"]


@pytest.mark.asyncio
async def test_unreadable_owners_fail_closed(test_db, monkeypatch):
    from app.repository.push_subscriptions import PushSubscriptionRepository

    await _device("root", "admin", "https://push.example/root")

    async def broken():
        raise RuntimeError("db gone")

    monkeypatch.setattr(PushSubscriptionRepository, "deliverable", staticmethod(broken))
    transport = Capture()
    dispatcher = _dispatcher(transport)

    await dispatcher.emit(None, payload=_keyword(), targets=["builtin:push"])
    await dispatcher.wait_idle()
    assert transport.calls == []


# -- the producer ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_keyword_rule_routes_web_push_with_the_message_audience(test_db, monkeypatch):
    from app.notifications.producers import emit_message
    from app.repository.notification_channels import NotificationChannelRepository

    seen: list[dict] = []

    async def fake_emit(event, *, payload=None, targets=None, push_already=None, **_kw):
        seen.append({"event": event.event, "targets": targets, "already": push_already})
        return len(targets or [])

    monkeypatch.setattr("app.notifications.producers.emit", fake_emit)
    monkeypatch.setattr("app.notifications.producers._radio_name", lambda: "Bob")
    rules = await NotificationChannelRepository.get_rules()
    rules["keywords"] = [
        {
            "id": "r1",
            "pattern": "secours",
            "match": "contains",
            "scope": "all",
            "channels": ["builtin:push"],
            "cooldown_s": 0,
        }
    ]
    await NotificationChannelRepository.set_rules(rules)

    await emit_message(
        {"type": "CHAN", "conversation_key": KEY, "text": "Léa: secours", "sender_name": "Léa"}
    )
    [keyword] = [s for s in seen if s["event"] == "message.keyword"]
    assert keyword["targets"] == ["builtin:push"]
    assert isinstance(keyword["already"], PushAudience)


# -- the device side -----------------------------------------------------------------------


def _push_manager(monkeypatch, subs: list[dict]):
    from app.push import manager as manager_module
    from app.push.manager import PushManager, _SendResult

    async def deliverable():
        return subs

    async def no_settings():
        raise RuntimeError("no settings")

    monkeypatch.setattr(
        manager_module.PushSubscriptionRepository, "deliverable", staticmethod(deliverable)
    )
    monkeypatch.setattr(manager_module, "get_vapid_private_key", lambda: "key")
    monkeypatch.setattr(manager_module.AppSettingsRepository, "get", staticmethod(no_settings))
    sent: dict[str, dict] = {}
    pm = PushManager()

    async def send_one(sub, payload, vapid_key, allow_private=False):
        sent[sub["id"]] = json.loads(payload)
        return _SendResult(sub["id"], success=True)

    async def no_outcomes(results, labels=None):
        return None

    monkeypatch.setattr(pm, "_send_one", send_one)
    monkeypatch.setattr(pm, "_record_outcomes", no_outcomes)
    return pm, sent


_SUBS = [
    {"id": "a", "user_id": 1, "owner_role": "admin", "label": "", "language": "en"},
    {"id": "m", "user_id": 2, "owner_role": "member", "label": "", "language": "en"},
    {"id": "u", "user_id": 3, "owner_role": None, "label": "", "language": "en"},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [_keyword(), _node("node_seen"), _node("node_back")])
async def test_the_device_side_never_sends_an_instance_rule_push_to_a_member(monkeypatch, payload):
    pm, sent = _push_manager(monkeypatch, _SUBS)

    await pm.dispatch_event(payload)
    assert list(sent) == ["a"]
    sent.clear()
    # Even an audience that names the member (a demotion between emit and send).
    await pm.dispatch_event(payload, PushAudience(default=payload, per_user={2: payload}))
    assert list(sent) == ["a"]


@pytest.mark.parametrize(
    ("role", "message_type", "allowed"),
    [
        ("admin", "PRIV", True),
        ("member", "PRIV", True),
        ("viewer", "PRIV", True),
        ("kiosk", "PRIV", False),
        ("kiosk", "CHAN", True),
        (None, "CHAN", False),
        ("ghost", "CHAN", False),
    ],
)
def test_who_may_read_a_message(role, message_type, allowed):
    assert may_read_message(role, {"type": message_type}) is allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["en", "fr"])
async def test_no_text_reaches_a_device_whose_owner_may_not_read_it(monkeypatch, lang):
    subs = [
        {"id": "a", "user_id": 1, "owner_role": "admin", "label": "", "language": lang},
        {"id": "k", "user_id": 4, "owner_role": "kiosk", "label": "", "language": lang},
    ]
    pm, sent = _push_manager(monkeypatch, subs)
    secret = {
        "type": "PRIV",
        "conversation_key": "cd" * 32,
        "sender_name": "Léa",
        "text": "the secret plan",
    }

    await pm.dispatch_event(secret)
    assert "the secret plan" in json.dumps(sent["a"], ensure_ascii=False)
    leaked = json.dumps(sent["k"], ensure_ascii=False)
    assert "secret plan" not in leaked and "Léa" not in leaked
    assert sent["k"]["title"] == ("New message" if lang == "en" else "Nouveau message")


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_a_redacted_keyword_alert_keeps_no_text(lang):
    redacted = redact_message(_keyword())
    assert set(redacted) <= {
        "event",
        "type",
        "conversation_key",
        "keyword_id",
        "mention",
        "redacted",
    }
    rendered = json.loads(build_push_payload(redacted, lang))
    assert rendered["title"] == ("Keyword alert" if lang == "en" else "Alerte mot-clé")
    for leak in ("secours", "Léa", "#paris"):
        assert leak not in json.dumps(rendered, ensure_ascii=False)


def test_a_long_message_is_clipped_for_the_push_service():
    rendered = json.loads(
        build_push_payload(_keyword(text="x" * 5000, channel_name="#" + "c" * 400), "en")
    )
    assert len(rendered["title"]) <= PUSH_TITLE_MAX
    assert len(rendered["body"]) <= PUSH_BODY_MAX
    assert rendered["body"].endswith("…")
    assert len(json.dumps(rendered).encode()) < 3000


# -- eviction --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_device_is_still_evicted_on_a_keyword_push(test_db, monkeypatch):
    import app.notifications.dispatcher as dispatcher_module
    from app.push import manager as manager_module
    from app.push.manager import PushManager, _SendResult
    from app.repository.push_subscriptions import (
        MAX_CONSECUTIVE_FAILURES,
        PushSubscriptionRepository,
    )

    emitted: list[dict] = []

    async def capture(event, *, payload=None, targets=None, **_kwargs):
        emitted.append(payload)
        return len(targets or [])

    monkeypatch.setattr(dispatcher_module, "emit", capture)
    monkeypatch.setattr(manager_module, "get_vapid_private_key", lambda: "key")
    await _device("root", "admin", "https://push.example/root")
    async with test_db.tx() as conn:
        await conn.execute(
            "UPDATE push_subscriptions SET failure_count = ?", (MAX_CONSECUTIVE_FAILURES - 1,)
        )
    pm = PushManager()

    async def failing(sub, payload, vapid_key, allow_private=False):
        return _SendResult(sub["id"])

    monkeypatch.setattr(pm, "_send_one", failing)

    await pm.dispatch_event(_keyword())
    assert await PushSubscriptionRepository.get_all() == []
    assert [p["error"] for p in emitted] == [failures.PUSH_EVICTED]
