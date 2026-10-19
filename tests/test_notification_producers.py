"""Every producer emits the right catalogue event to the dispatcher, and what reaches Web Push
through the rules is byte-identical to the MSHL-107 golden fixtures."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Contact
from app.notifications import events as ev
from app.notifications.dispatcher import notification_dispatcher
from app.repository.channels import ChannelRepository
from app.repository.push_subscriptions import PushSubscriptionRepository
from app.repository.settings import AppSettingsRepository

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((ROOT / "tests" / "fixtures" / "notifications_golden.json").read_text("utf-8"))
PUBLIC_KEY = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"
HASHTAG_KEY = "A1B2C3D4E5F60718293A4B5C6D7E8F90"
PRIVATE_KEY = "00112233445566778899AABBCCDDEEFF"


@pytest.fixture
def emitted(monkeypatch) -> list[dict]:
    """What the producers hand to ``emit``: the event, the payload and the exclusions."""
    seen: list[dict] = []

    async def fake_emit(event, *, payload=None, targets=None, exclude=(), **_):
        seen.append({"event": event, "payload": dict(payload or {}), "exclude": tuple(exclude)})
        return 1

    monkeypatch.setattr("app.notifications.producers.emit", fake_emit)
    return seen


def _dm(**extra) -> dict:
    return {
        "type": "PRIV",
        "conversation_key": "aa" * 32,
        "sender_key": "aa" * 32,
        "sender_name": "Alice",
        "text": "hello",
        "outgoing": False,
        **extra,
    }


# ── WebSocket message hook ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_websocket_message_hook_emits_a_message_event(test_db, emitted):
    from app.websocket import broadcast_event

    with patch("app.websocket.ws_manager"), patch("app.fanout.manager.fanout_manager") as fm:
        fm.broadcast_message = AsyncMock()
        broadcast_event("message", _dm())
        for _ in range(10):
            await asyncio.sleep(0)
    assert len(emitted) == 1
    assert isinstance(emitted[0]["event"], ev.MessageEvent)
    assert emitted[0]["event"].event == "message.dm"
    assert emitted[0]["payload"]["event"] == "message"  # the payload the transports render


@pytest.mark.asyncio
async def test_websocket_hook_is_silent_without_realtime(emitted):
    from app.websocket import broadcast_event

    with patch("app.websocket.ws_manager"):
        broadcast_event("message", _dm(), realtime=False)
        await asyncio.sleep(0)
    assert emitted == []


@pytest.mark.asyncio
async def test_channel_message_is_message_channel(test_db, emitted):
    from app.notifications.producers import emit_message

    await emit_message(
        {"type": "CHAN", "conversation_key": PUBLIC_KEY, "text": "hi", "sender_name": "Bob"}
    )
    assert emitted[0]["event"].event == "message.channel"


@pytest.mark.asyncio
async def test_outgoing_and_pending_messages_never_notify(test_db, emitted):
    from app.notifications.producers import emit_message

    await emit_message(_dm(outgoing=True))
    await ChannelRepository.upsert(HASHTAG_KEY, "#new", is_hashtag=True, membership="pending")
    await emit_message({"type": "CHAN", "conversation_key": HASHTAG_KEY, "text": "x"})
    assert emitted == []


@pytest.mark.asyncio
async def test_muted_channel_is_silent_unless_always_deliver(test_db, emitted):
    from app.notifications.producers import emit_message

    await ChannelRepository.upsert(HASHTAG_KEY, "#ops", is_hashtag=True)
    await ChannelRepository.set_muted(HASHTAG_KEY, True)
    message = {"type": "CHAN", "conversation_key": HASHTAG_KEY, "text": "x"}
    await emit_message(message)
    assert emitted == []

    from app.repository.notification_channels import NotificationChannelRepository

    rules = await NotificationChannelRepository.get_rules()
    rules["events"]["message.channel"]["always_deliver"] = True
    await NotificationChannelRepository.set_rules(rules)
    await emit_message(message)
    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_blocked_sender_skips_the_apprise_channel_only(test_db, emitted):
    from app.notifications.producers import emit_message
    from app.repository.notification_channels import NotificationChannelRepository

    rules = await NotificationChannelRepository.get_rules()
    rules["events"]["message.dm"]["channels"] = ["builtin:push", "legacy:apprise"]
    await NotificationChannelRepository.set_rules(rules)
    await AppSettingsRepository.update(blocked_keys=["aa" * 32])
    await emit_message(_dm())
    assert emitted[0]["exclude"] == ("legacy:apprise",)


# ── The other producers ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_seen_emits_a_first_seen_event(test_db, emitted, monkeypatch):
    from app.push.first_seen import maybe_notify_contact_first_seen
    from app.radio import radio_manager

    monkeypatch.setattr(radio_manager, "_setup_complete", True)
    await maybe_notify_contact_first_seen(
        Contact(public_key="ab" * 32, name="Rpt", type=2), origin="rf_advert"
    )
    event = emitted[0]["event"]
    assert isinstance(event, ev.FirstSeenEvent) and event.data.contact_type == 2
    assert emitted[0]["payload"]["event"] == "first_seen"

    emitted.clear()  # a room or an unknown type, and the radio setup, stay silent
    await maybe_notify_contact_first_seen(Contact(public_key="ac" * 32, type=3), origin="rf_advert")
    monkeypatch.setattr(radio_manager, "_setup_complete", False)
    await maybe_notify_contact_first_seen(Contact(public_key="ad" * 32, type=1), origin="rf_advert")
    assert emitted == []


@pytest.mark.asyncio
async def test_channel_found_emits_a_channel_found_event(test_db, emitted):
    from app.services.hashtag_catalogue import _notify_channel_found

    await _notify_channel_found("#meshloom", PUBLIC_KEY)
    event = emitted[0]["event"]
    assert isinstance(event, ev.ChannelFoundEvent) and event.data.name == "#meshloom"


@pytest.mark.asyncio
async def test_telemetry_alert_emits_an_urgent_telemetry_event(test_db, emitted):
    from app.telemetry_alerts import _dispatch

    await _dispatch(
        public_key="ab" * 32, name="Rpt", rule_id="battery", value=3.2, threshold=3.5, op="lt"
    )
    event = emitted[0]["event"]
    assert isinstance(event, ev.TelemetryAlertEvent) and event.urgent
    assert emitted[0]["payload"]["metric"] == "battery"  # enriched as before


@pytest.mark.asyncio
async def test_oss_update_emits_an_update_event_and_keeps_its_guard(test_db, emitted):
    import app.services.oss_updates as oss_updates
    from app.version_info import AppBuildInfo

    oss_updates.reset_oss_update_cache()
    oss_updates._latest_payload = {"version": "9.9.9", "html_url": None}
    info = AppBuildInfo(version="1.0.0", version_source="t", commit_hash=None, commit_source=None)
    with patch("app.services.oss_updates.get_app_build_info", return_value=info):
        await oss_updates._maybe_notify_oss_update()
        await oss_updates._maybe_notify_oss_update()
    assert len(emitted) == 1  # last_notified_update_version
    assert isinstance(emitted[0]["event"], ev.AppUpdateEvent)
    assert emitted[0]["event"].data.latest == "9.9.9"
    assert await AppSettingsRepository.get_last_notified_update_version() == "9.9.9"


@pytest.mark.asyncio
async def test_oss_update_off_is_not_recorded(test_db):
    from app.notifications.producers import emit_oss_update

    await AppSettingsRepository.set_push_defaults({"oss_update": False})
    assert await emit_oss_update("1.0.0", "9.9.9") is False


# ── Web push payloads equal the golden fixtures ──────────────────────────


def _cases() -> dict[str, dict]:
    path = ROOT / "scripts" / "dev" / "gen_notifications_golden.py"
    spec = importlib.util.spec_from_file_location("gen_notifications_golden", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.EVENTS


# Grouped summaries are dispatcher output; a room or an unknown contact type, a private-key
# channel and an unknown event never notify.
NOT_DELIVERED = {
    "first_seen_grouped",
    "notifications_grouped",
    "first_seen_empty",
    "message_channel_private",
    "message_channel_unnamed",
    "destination_changed",
    "unknown_event",
}


@pytest.mark.asyncio
async def test_push_payloads_through_the_rules_match_the_golden_fixtures(test_db, monkeypatch):
    from app.notifications.producers import emit_message, emit_payload

    for lang in ("fr", "en"):
        await PushSubscriptionRepository.create(
            endpoint=f"https://push.example.test/{lang}", p256dh="p", auth="a", language=lang
        )
    sent: list[dict] = []

    async def fake_send(**kwargs):
        sent.append(kwargs)
        return 201

    monkeypatch.setattr("app.push.manager.get_vapid_private_key", lambda: "k")
    monkeypatch.setattr("app.push.manager.send_push", fake_send)
    await ChannelRepository.upsert(HASHTAG_KEY, "#test", is_hashtag=True)

    delivered = set()
    for name, payload in _cases().items():
        sent.clear()
        if payload.get("type") in ("PRIV", "CHAN"):
            await emit_message(payload)
        else:
            await emit_payload(payload)
        await notification_dispatcher().wait_idle()
        if name in NOT_DELIVERED:
            assert sent == [], name
            continue
        delivered.add(name)
        by_lang = {s["subscription_info"]["endpoint"].rsplit("/", 1)[1]: s["payload"] for s in sent}
        assert set(by_lang) == {"fr", "en"}, name
        for lang, body in by_lang.items():
            assert body == GOLDEN[name][lang]["push_raw"], (name, lang)
    assert {
        "message_dm",
        "message_channel_public",
        "channel_found",
        "telemetry_battery",
    } <= delivered


# ── review fixes: delivered only, hot path ───────────────────────────────


@pytest.mark.asyncio
async def test_oss_update_is_not_recorded_when_nothing_is_queued(test_db, monkeypatch):
    import time

    from app.notifications.producers import emit_oss_update
    from app.push.manager import PushOutcome, push_manager
    from app.repository.notification_channels import NotificationChannelRepository

    monkeypatch.setattr(push_manager, "dispatch_event", AsyncMock(return_value=PushOutcome()))
    rules = await NotificationChannelRepository.get_rules()
    rules["paused_until"] = int(time.time()) + 3600  # the global pause holds app.update
    await NotificationChannelRepository.set_rules(rules)
    assert await emit_oss_update("1.0.0", "9.9.9") is False
    rules["paused_until"] = None
    await NotificationChannelRepository.set_rules(rules)
    assert await emit_oss_update("1.0.0", "9.9.9") is True


@pytest.mark.asyncio
async def test_oss_update_to_a_disabled_channel_is_not_recorded(test_db):
    from app.notifications.channels import ChannelUpdate
    from app.notifications.producers import emit_oss_update
    from app.repository.notification_channels import NotificationChannelRepository

    rules = await NotificationChannelRepository.get_rules()
    assert rules["events"]["app.update"]["channels"] == ["builtin:push"]
    await NotificationChannelRepository.update("builtin:push", ChannelUpdate(enabled=False))
    assert await emit_oss_update("1.0.0", "9.9.9") is False


@pytest.mark.asyncio
async def test_a_message_reads_the_channel_once_and_the_settings_only_for_apprise(
    test_db, monkeypatch
):
    from app.notifications.producers import emit_message

    lookups: list[str] = []
    real = ChannelRepository.get_by_key

    async def counting(key):
        lookups.append(key)
        return await real(key)

    reads: list[int] = []
    real_get = AppSettingsRepository.get

    async def counting_get(*a, **k):
        reads.append(1)
        return await real_get(*a, **k)

    monkeypatch.setattr(ChannelRepository, "get_by_key", counting)
    monkeypatch.setattr(AppSettingsRepository, "get", counting_get)
    await ChannelRepository.upsert(HASHTAG_KEY, "#ops", is_hashtag=True)
    await emit_message({"type": "CHAN", "conversation_key": HASHTAG_KEY, "text": "x"})
    assert lookups == [HASHTAG_KEY]  # not looked up again by the dispatcher
    assert reads == []  # no Apprise channel is a candidate
