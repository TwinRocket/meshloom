import json
from unittest.mock import AsyncMock

import pytest

from app.email_template import (
    LOGO_URL,
    email_subject,
    enrich_notification_payload,
    render_html_email,
    render_plain_email,
    resolve_instance_language,
)
from app.models import NotificationDestinations, NotificationWebhookDest
from app.notify import TEST_ALERT_PAYLOAD, send_webhook_alert
from app.repository.settings import coerce_notification_media, or_notification_media


def test_test_alert_payload_identifies_node_and_metric():
    assert len(TEST_ALERT_PAYLOAD["public_key"]) == 64
    assert TEST_ALERT_PAYLOAD["name"] == "Test repeater"
    assert TEST_ALERT_PAYLOAD["rule_id"] == "battery"
    assert TEST_ALERT_PAYLOAD["metric"] == "battery"
    assert TEST_ALERT_PAYLOAD["unit"] == "V"
    assert TEST_ALERT_PAYLOAD["value"] == 3.46
    assert TEST_ALERT_PAYLOAD["threshold"] == 3.5
    assert TEST_ALERT_PAYLOAD["op"] == "lt"


def test_enrich_maps_lpp_rule_to_metric():
    out = enrich_notification_payload(
        {
            "event": "telemetry_alert",
            "public_key": "cc" * 32,
            "name": "Sensor-1",
            "rule_id": "lpp:temperature:1",
            "value": 21.5,
            "threshold": 20,
            "op": "gt",
        }
    )
    assert out["metric"] == "temperature"
    assert out["unit"] == "°C"
    assert out["public_key"] == "cc" * 32
    assert out["name"] == "Sensor-1"


def test_coerce_bool_sets_push_only():
    flags = coerce_notification_media(False)
    assert flags == {"push": False, "email": False, "webhook": False}
    flags = coerce_notification_media(True, {"push": False, "email": True, "webhook": True})
    assert flags["push"] is True
    assert flags["email"] is True
    assert flags["webhook"] is True


def test_or_notification_media_unions_flags():
    combined = or_notification_media(
        {"push": True, "email": False, "webhook": False},
        {"push": False, "email": True, "webhook": False},
    )
    assert combined == {"push": True, "email": True, "webhook": False}


@pytest.mark.asyncio
async def test_webhook_event_header_follows_payload(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, content=None, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["content"] = content
            return _FakeResponse()

    monkeypatch.setattr("app.notify.httpx.AsyncClient", _FakeClient)

    dest = NotificationDestinations(
        webhook=NotificationWebhookDest(url="https://example.test/hook")
    )
    await send_webhook_alert(dest, {"event": "new_dm", "text": "hi"})
    assert captured["headers"]["X-Webhook-Event"] == "new_dm"
    assert captured["url"] == "https://example.test/hook"


@pytest.mark.asyncio
async def test_webhook_telemetry_includes_node_and_metric(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, content=None, headers=None):
            captured["headers"] = headers
            captured["body"] = json.loads(content)
            return _FakeResponse()

    monkeypatch.setattr("app.notify.httpx.AsyncClient", _FakeClient)
    dest = NotificationDestinations(
        webhook=NotificationWebhookDest(url="https://example.test/hook")
    )
    await send_webhook_alert(
        dest,
        {
            "event": "telemetry_alert",
            "public_key": "bb" * 32,
            "name": "HillRepeater",
            "rule_id": "battery",
            "value": 3.46,
            "threshold": 3.5,
            "op": "lt",
        },
    )
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["public_key"] == "bb" * 32
    assert body["name"] == "HillRepeater"
    assert body["metric"] == "battery"
    assert body["unit"] == "V"
    assert body["value"] == 3.46
    assert body["rule_id"] == "battery"


@pytest.mark.asyncio
async def test_dispatch_uses_explicit_channels(monkeypatch):
    from app.notify import dispatch_system_event

    push = AsyncMock()
    email = AsyncMock()
    webhook = AsyncMock()
    monkeypatch.setattr("app.push.manager.push_manager.dispatch_event", push)
    monkeypatch.setattr("app.notify.send_email_alert", email)
    monkeypatch.setattr("app.notify.send_webhook_alert", webhook)
    monkeypatch.setattr(
        "app.repository.settings.AppSettingsRepository.get",
        AsyncMock(
            return_value=type("S", (), {"notification_destinations": NotificationDestinations()})()
        ),
    )

    await dispatch_system_event(
        {"event": "oss_update"}, {"push": False, "email": True, "webhook": True}
    )
    push.assert_not_called()
    email.assert_awaited_once()
    webhook.assert_awaited_once()


def test_telemetry_email_is_html_and_french_by_default():
    payload = {
        "event": "telemetry_alert",
        "name": "HillRepeater",
        "rule_id": "battery",
        "value": 3.46,
        "threshold": 3.5,
        "op": "lt",
    }
    plain = render_plain_email(payload, "fr")
    html = render_html_email(payload, "fr")
    assert "Une alerte a été déclenchée concernant : HillRepeater" in plain
    assert "Tension de la batterie : 3.46 V" in plain
    assert "Seuil réglé : inférieur à 3.5 V" in plain
    assert LOGO_URL in html
    assert "HillRepeater" in html
    assert "inférieur à 3.5 V" in html
    assert email_subject(payload, "fr").startswith("Batterie faible")


def test_telemetry_email_english_uses_below():
    payload = {
        "event": "telemetry_alert",
        "name": "HillRepeater",
        "rule_id": "battery",
        "value": 3.46,
        "threshold": 3.5,
        "op": "lt",
    }
    plain = render_plain_email(payload, "en")
    assert "An alert was raised for: HillRepeater" in plain
    assert "Battery voltage : 3.46 V" in plain
    assert "below 3.5 V" in plain


@pytest.mark.asyncio
async def test_instance_language_follows_push_subscription_majority(test_db):
    from app.repository.push_subscriptions import PushSubscriptionRepository

    await PushSubscriptionRepository.create(
        endpoint="https://push.example/en",
        p256dh="p",
        auth="a",
        language="en",
    )
    await PushSubscriptionRepository.create(
        endpoint="https://push.example/en-2",
        p256dh="p2",
        auth="a2",
        language="en",
    )
    await PushSubscriptionRepository.create(
        endpoint="https://push.example/fr",
        p256dh="p3",
        auth="a3",
        language="fr",
    )
    assert await resolve_instance_language() == "en"


@pytest.mark.asyncio
async def test_send_email_uses_html_body_format(monkeypatch):
    from app.models import NotificationEmailDest
    from app.notify import send_email_alert

    captured: dict[str, object] = {}

    def _fake_send(url: str, title: str, body: str) -> bool:
        captured["title"] = title
        captured["body"] = body
        captured["url"] = url
        return True

    monkeypatch.setattr("app.notify._send_email_sync", _fake_send)
    dest = NotificationDestinations(
        email=NotificationEmailDest(host="smtp.example", to="ops@example.com")
    )
    await send_email_alert(dest, TEST_ALERT_PAYLOAD)
    assert "<!DOCTYPE html>" in str(captured["body"])
    assert LOGO_URL in str(captured["body"])
    assert "Tension de la batterie : 3.46 V" in str(captured["body"])
    assert "Test repeater" in str(captured["body"])
