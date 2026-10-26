"""The event catalogue (contract version 1) and the typed renderer."""

import dataclasses
import re

import pytest

from app.notifications import events as ev
from app.notifications.events import EVENT_IDS, event_from_payload
from app.notifications.render import render
from app.push.manager import _build_payload, event_notification_text

PUBKEY = "ab" * 32
PUBLIC_KEY = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"

PAYLOADS = {
    "message.dm": {
        "event": "message",
        "type": "PRIV",
        "conversation_key": PUBKEY,
        "sender_name": "Léa",
        "text": "hi @everyone",
    },
    "message.channel": {
        "event": "message",
        "type": "CHAN",
        "conversation_key": PUBLIC_KEY,
        "channel_name": "Public",
        "text": "hello",
    },
    "message.mention": {
        "event": "message",
        "type": "CHAN",
        "conversation_key": PUBLIC_KEY,
        "channel_name": "Public",
        "text": "hello",
        "mention": True,
    },
    "contact.first_seen": {
        "event": "first_seen",
        "public_key": PUBKEY,
        "name": "Alice",
        "contact_type": 2,
    },
    "channel.found": {"event": "channel_found", "name": "#a", "channel_key": PUBLIC_KEY},
    "telemetry.alert": {
        "event": "telemetry_alert",
        "rule_id": "battery",
        "public_key": PUBKEY,
        "name": "N",
        "value": 3.4,
        "threshold": 3.5,
        "op": "lt",
    },
    "radio.offline": {"event": "radio_offline", "offline_s": 90, "connection": "TCP: h:1"},
    "radio.online": {"event": "radio_online", "downtime_s": 90},
    "app.started": {"event": "app_started", "version": "5.0.0", "radio_connected": True},
    "app.update": {"event": "oss_update", "current": "5.0.0", "latest": "5.1.0"},
    "notify.destination_changed": {
        "event": "notify.destination_changed",
        "kinds": ["email"],
    },
    "notify.channel_failed": {
        "event": "notify.channel_failed",
        "channel_id": "ntfy-1",
        "name": "ntfy",
        "error": "auth_failed",
    },
    "notify.heartbeat": {
        "event": "notify.heartbeat",
        "instance": "Base",
        "uptime_s": 3600,
        "version": "5.0.0",
    },
}


def test_every_event_id_is_unique_and_domain_dot_name():
    assert len(set(EVENT_IDS)) == len(EVENT_IDS)
    assert all(re.fullmatch(r"[a-z]+\.[a-z_]+", i) for i in EVENT_IDS)
    assert ev.CONTRACT_VERSION == 1 == ev.NotificationEvent.version


@pytest.mark.parametrize("event_id", PAYLOADS)
def test_payload_maps_to_its_catalogue_event_and_back(event_id):
    payload = PAYLOADS[event_id]
    event = event_from_payload(payload, event_id="e1", occurred_at=5)
    assert event is not None
    assert event.event == event_id and event_id in EVENT_IDS
    assert (event.event_id, event.occurred_at) == ("e1", 5)
    assert dataclasses.is_dataclass(event) and event.__dataclass_params__.frozen  # type: ignore[attr-defined]
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.urgent = True  # type: ignore[misc]
    back = event.to_payload()
    # Rendering the round-tripped payload is the same as rendering the original.
    for lang in ("fr", "en"):
        assert _build_payload(back, lang) == _build_payload(payload, lang)


def test_grouped_summaries_and_unknown_events_are_not_catalogue_events():
    assert event_from_payload({"event": "notifications_grouped", "count": 3}) is None
    assert event_from_payload({"event": "first_seen_grouped", "count": 3}) is None
    assert event_from_payload({"event": "nope"}) is None


def test_urgent_defaults():
    assert event_from_payload(PAYLOADS["telemetry.alert"]).urgent is True  # type: ignore[union-attr]
    assert event_from_payload(PAYLOADS["radio.offline"]).urgent is True  # type: ignore[union-attr]
    for event_id in ("message.dm", "message.channel", "message.mention", "contact.first_seen"):
        assert event_from_payload(PAYLOADS[event_id]).urgent is False  # type: ignore[union-attr]


@pytest.mark.parametrize("event_id", PAYLOADS)
def test_dedup_key_is_a_short_hash_never_a_public_key(event_id):
    event = event_from_payload(PAYLOADS[event_id])
    assert event is not None
    assert re.fullmatch(r"[0-9a-f]{16}", event.dedup_key)
    assert PUBKEY not in event.dedup_key and PUBKEY[:12] not in event.dedup_key


def test_message_dedup_key_does_not_contain_the_contact_public_key():
    event = event_from_payload(PAYLOADS["message.dm"])
    assert event is not None and event.conversation_key == PUBKEY
    assert PUBKEY not in event.dedup_key
    other = event_from_payload({**PAYLOADS["message.dm"], "text": "other"})
    assert other is not None and other.dedup_key != event.dedup_key
    same = event_from_payload(PAYLOADS["message.dm"])
    assert same is not None and same.dedup_key == event.dedup_key


def test_message_event_ids_follow_the_conversation_type():
    assert event_from_payload(PAYLOADS["message.dm"]).event == "message.dm"  # type: ignore[union-attr]
    assert event_from_payload(PAYLOADS["message.channel"]).event == "message.channel"  # type: ignore[union-attr]


def test_the_test_event_renders_the_test_alert():
    event = ev.TestEvent(event="notify.test", dedup_key="x", data=ev.TestData())
    rendered = render(event, "en")
    assert rendered.title == "Low battery: Test repeater"


@pytest.mark.parametrize("lang", ["fr", "en"])
@pytest.mark.parametrize("event_id", PAYLOADS)
def test_render_matches_the_legacy_helpers(event_id, lang):
    payload = PAYLOADS[event_id]
    event = event_from_payload(payload)
    assert event is not None
    rendered = render(event, lang)
    title, body = event_notification_text(payload, lang)
    _, md = event_notification_text(payload, lang, markdown=True)
    pushed = __import__("json").loads(_build_payload(payload, lang))
    assert (rendered.title, rendered.body_text, rendered.body_markdown) == (title, body, md)
    assert (rendered.tag, rendered.url_hash) == (pushed["tag"], pushed.get("url_hash", ""))
    assert rendered.body_html.startswith("<!DOCTYPE html>")


def test_render_applies_the_text_policy_once_by_conversation_class():
    dm = event_from_payload(PAYLOADS["message.dm"])
    public = event_from_payload(PAYLOADS["message.channel"])
    assert dm is not None and public is not None
    assert render(dm, "en", text_policy="always").body_text.startswith("hi @")
    assert render(dm, "en", text_policy="public_only").body_text == "Message from Léa"
    assert render(public, "en", text_policy="public_only").body_text == "hello"
    assert render(public, "en", text_policy="never").body_text == "New message"
    assert render(dm, "fr", text_policy="never").body_text == "Message de Léa"


def test_render_neutralises_mentions_and_escapes_markdown():
    dm = event_from_payload({**PAYLOADS["message.dm"], "text": "a @everyone <@1> *b*"})
    assert dm is not None
    rendered = render(dm, "en")
    assert "@everyone" not in rendered.body_text and "<@1>" not in rendered.body_text
    assert "\\*b\\*" in rendered.body_markdown


def test_unsupported_language_falls_back_to_french():
    event = event_from_payload(PAYLOADS["app.update"])
    assert event is not None
    assert (
        render(event, "de").title == render(event, "fr").title == "Mise à jour Meshloom disponible"
    )
