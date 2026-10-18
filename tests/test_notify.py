import json
import logging
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.email_template import (
    LOGO_CID,
    LOGO_PATH,
    email_subject,
    enrich_notification_payload,
    render_html_email,
    render_plain_email,
    resolve_instance_language,
)
from app.models import AppSettings, NotificationDestinations, NotificationWebhookDest
from app.net.egress_guard import EgressBlocked
from app.notify import (
    TEST_ALERT_PAYLOAD,
    dispatch_system_event,
    send_webhook_alert,
    webhook_last_error,
)
from app.repository.settings import (
    AppSettingsRepository,
    coerce_notification_media,
    or_notification_media,
)


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
    assert flags == {"push": False, "email": False, "webhook": False, "apprise": False}
    flags = coerce_notification_media(True, {"push": False, "email": True, "webhook": True})
    assert flags["push"] is True
    assert flags["email"] is True
    assert flags["webhook"] is True


def test_or_notification_media_unions_flags():
    combined = or_notification_media(
        {"push": True, "email": False, "webhook": False},
        {"push": False, "email": True, "webhook": False},
    )
    assert combined == {"push": True, "email": True, "webhook": False, "apprise": False}


@pytest.fixture
def webhook_requests(monkeypatch) -> list[httpx.Request]:
    """Requests the pinned webhook client sends, answered 204 without a socket."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204)

    real = httpx.AsyncClient

    class Recording(real):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Recording)
    return captured


@pytest.mark.asyncio
async def test_webhook_event_header_follows_payload(webhook_requests):
    dest = NotificationDestinations(
        webhook=NotificationWebhookDest(url="https://93.184.215.14/hook")
    )
    await send_webhook_alert(dest, {"event": "new_dm", "text": "hi"})
    (request,) = webhook_requests
    assert request.headers["X-Webhook-Event"] == "new_dm"
    assert str(request.url) == "https://93.184.215.14/hook"


@pytest.mark.asyncio
async def test_webhook_telemetry_includes_node_and_metric(webhook_requests):
    dest = NotificationDestinations(
        webhook=NotificationWebhookDest(url="https://93.184.215.14/hook")
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
    body = json.loads(webhook_requests[0].content)
    assert isinstance(body, dict)
    assert body["public_key"] == "bb" * 32
    assert body["name"] == "HillRepeater"
    assert body["metric"] == "battery"
    assert body["unit"] == "V"
    assert body["value"] == 3.46
    assert body["rule_id"] == "battery"


# ── Egress guard on the system webhook ───────────────────────────────────

LAN_HOOK = "http://192.168.1.10/"


@pytest.mark.asyncio
async def test_lan_webhook_refused_at_send_without_the_flag(webhook_requests):
    dest = NotificationDestinations(webhook=NotificationWebhookDest(url=LAN_HOOK))
    with pytest.raises(EgressBlocked) as caught:
        await send_webhook_alert(dest, {"event": "new_dm"})
    assert caught.value.code == "egress_private_address"
    assert webhook_requests == []
    assert webhook_last_error() is not None
    assert webhook_last_error()["code"] == "egress_private_address"


@pytest.mark.asyncio
async def test_lan_webhook_sent_with_the_flag_and_last_error_cleared(webhook_requests):
    dest = NotificationDestinations(
        webhook=NotificationWebhookDest(url=LAN_HOOK, allow_private_network=True)
    )
    await send_webhook_alert(dest, {"event": "new_dm"})
    assert webhook_requests[0].url.host == "192.168.1.10"
    assert webhook_last_error() is None


@pytest.mark.asyncio
async def test_dispatch_logs_a_refused_webhook_as_a_warning(webhook_requests, monkeypatch, caplog):
    monkeypatch.setattr(
        "app.notify.AppSettingsRepository.get",
        AsyncMock(
            return_value=SimpleNamespace(
                notification_destinations=NotificationDestinations(
                    webhook=NotificationWebhookDest(url=LAN_HOOK + "tok3n")
                )
            )
        ),
    )
    with caplog.at_level(logging.WARNING, logger="app.notify"):
        await dispatch_system_event({"event": "oss_update"}, {"webhook": True})
    assert "webhook dispatch failed: egress_private_address" in caplog.text
    assert "tok3n" not in caplog.text
    assert webhook_requests == []


@pytest.mark.asyncio
async def test_lan_webhook_refused_at_save_without_the_flag(test_db, client):
    response = await client.patch(
        "/api/settings", json={"notification_destinations": {"webhook": {"url": LAN_HOOK}}}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "egress_private_address"
    assert "192.168" not in response.text
    stored = await AppSettingsRepository.get()
    assert stored.notification_destinations.webhook.url == ""


@pytest.mark.asyncio
async def test_lan_webhook_saved_with_the_flag(test_db, client):
    response = await client.patch(
        "/api/settings",
        json={
            "notification_destinations": {
                "webhook": {"url": LAN_HOOK, "allow_private_network": True}
            }
        },
    )
    assert response.status_code == 200
    webhook = response.json()["notification_destinations"]["webhook"]
    assert webhook["allow_private_network"] is True
    assert webhook["private_network_grandfathered"] is False
    # Turning the flag off again is refused while the URL stays on the LAN.
    response = await client.patch(
        "/api/settings",
        json={"notification_destinations": {"webhook": {"allow_private_network": False}}},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_unchanged_webhook_is_not_revalidated_on_save(test_db, client, monkeypatch):
    await _store_raw_destinations(test_db, {"webhook": {"url": "https://hook.lan.invalid/x"}})
    guard = AsyncMock(side_effect=AssertionError("revalidated"))
    monkeypatch.setattr("app.routers.settings.validate_destination", guard)
    response = await client.patch(
        "/api/settings",
        json={"notification_destinations": {"email": {"host": "smtp.example.test"}}},
    )
    assert response.status_code == 200
    guard.assert_not_called()


async def _store_raw_destinations(db, raw: dict) -> None:
    """Write notification_destinations as a release before the guard stored it."""
    async with db.tx() as conn:
        await conn.execute(
            "UPDATE app_settings SET notification_destinations = ? WHERE id = 1",
            (json.dumps(raw),),
        )


@pytest.mark.asyncio
async def test_existing_lan_webhook_keeps_working_after_the_upgrade(test_db, webhook_requests):
    await _store_raw_destinations(test_db, {"webhook": {"url": LAN_HOOK, "hmac_secret": ""}})
    dest = (await AppSettingsRepository.get()).notification_destinations
    assert dest.webhook.allow_private_network is True
    assert dest.webhook.private_network_grandfathered is True
    await send_webhook_alert(dest, {"event": "new_dm"})
    assert webhook_requests[0].url.host == "192.168.1.10"


@pytest.mark.asyncio
async def test_grandfathering_banner_dismissed_and_new_url_drops_it(test_db, client):
    await _store_raw_destinations(test_db, {"webhook": {"url": LAN_HOOK}})
    settings = (await client.get("/api/settings")).json()
    assert settings["notification_destinations"]["webhook"]["private_network_grandfathered"]
    response = await client.patch(
        "/api/settings",
        json={"notification_destinations": {"webhook": {"private_network_grandfathered": False}}},
    )
    webhook = response.json()["notification_destinations"]["webhook"]
    assert webhook["private_network_grandfathered"] is False
    assert webhook["allow_private_network"] is True
    # Stored explicitly now: the lazy default never applies again.
    stored = (await AppSettingsRepository.get()).notification_destinations.webhook
    assert stored.private_network_grandfathered is False

    await _store_raw_destinations(test_db, {"webhook": {"url": LAN_HOOK}})
    response = await client.patch(
        "/api/settings",
        json={"notification_destinations": {"webhook": {"url": "https://93.184.215.14/x"}}},
    )
    assert (
        response.json()["notification_destinations"]["webhook"]["private_network_grandfathered"]
        is False
    )


@pytest.mark.asyncio
async def test_new_webhook_and_empty_url_are_not_grandfathered(test_db):
    await _store_raw_destinations(test_db, {"webhook": {"url": ""}})
    assert not (
        await AppSettingsRepository.get()
    ).notification_destinations.webhook.allow_private_network
    await _store_raw_destinations(
        test_db, {"webhook": {"url": LAN_HOOK, "allow_private_network": False}}
    )
    webhook = (await AppSettingsRepository.get()).notification_destinations.webhook
    assert webhook.allow_private_network is False
    assert webhook.private_network_grandfathered is False


def test_backup_from_before_the_guard_restores_the_lan_opt_in():
    settings = AppSettings.model_validate(
        {"notification_destinations": {"webhook": {"url": LAN_HOOK}}}
    )
    assert settings.notification_destinations.webhook.allow_private_network is True
    fresh = NotificationWebhookDest(url=LAN_HOOK)
    assert fresh.allow_private_network is False


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
    assert f'src="cid:{LOGO_CID}"' in html
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
    assert f"cid:{LOGO_CID}" in str(captured["body"])
    assert "Tension de la batterie : 3.46 V" in str(captured["body"])
    assert "Test repeater" in str(captured["body"])


_EVENTS_FOR_EMAIL = [
    TEST_ALERT_PAYLOAD,
    {"event": "first_seen", "name": "Hill", "contact_type": 2},
    {"event": "channel_found", "name": "#meshcore"},
    {"event": "oss_update", "current": "4.20.0", "latest": "4.21.0"},
    {"event": "message", "type": "CHAN", "sender_name": "Bob", "text": "hi"},
]


@pytest.mark.parametrize("payload", _EVENTS_FOR_EMAIL)
@pytest.mark.parametrize("lang", ["fr", "en"])
def test_html_email_references_no_remote_resource(payload, lang):
    """Opening an alert email must fetch nothing: no URL at all in the HTML."""
    html = render_html_email(payload, lang)
    assert "meshloom.app" not in html
    assert "http://" not in html and "https://" not in html
    assert "href=" not in html
    assert re.findall(r'src="([^"]+)"', html) == [f"cid:{LOGO_CID}"]


def test_logo_asset_is_a_small_png_shipped_in_the_package():
    data = LOGO_PATH.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) < 16_000
    assert LOGO_PATH.parent.parent.name == "app"


class _CapturingSMTP:
    """Stands in for smtplib.SMTP: keeps the message Apprise would send."""

    sent: list[str] = []

    def __init__(self, *_args, **_kwargs):
        pass

    def ehlo(self, *_args):
        return (250, b"ok")

    def starttls(self, *_args, **_kwargs):
        return (220, b"ok")

    def login(self, *_args):
        return (235, b"ok")

    def sendmail(self, _from, _to, message):
        self.sent.append(message)
        return {}

    def quit(self):
        return (221, b"bye")

    def close(self):
        return None


def test_email_embeds_the_logo_inline_as_multipart_related(monkeypatch):
    """The real Apprise MIME: related(alternative(text, html), inline PNG cid)."""
    import email
    import smtplib
    from email import policy

    from app.notify import _send_email_sync, build_email_apprise_url

    _CapturingSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _CapturingSMTP)
    url = build_email_apprise_url(
        {
            "mode": "none",
            "host": "smtp.example",
            "port": 25,
            "from": "meshloom@example.com",
            "to": "ops@example.com",
        }
    )
    assert "inline=yes" in url
    assert _send_email_sync(url, "Alert", render_html_email(TEST_ALERT_PAYLOAD, "fr")) is True
    assert len(_CapturingSMTP.sent) == 1
    raw = _CapturingSMTP.sent[0]

    message = email.message_from_string(raw, policy=policy.default)
    assert message.get_content_type() == "multipart/related"
    parts = list(message.iter_parts())
    assert [p.get_content_type() for p in parts] == ["multipart/alternative", "image/png"]
    alternative, logo = parts
    assert [p.get_content_type() for p in alternative.iter_parts()] == [
        "text/plain",
        "text/html",
    ]
    assert logo["Content-ID"] == f"<{LOGO_CID}>"
    assert logo.get_content_disposition() == "inline"
    assert logo.get_content() == LOGO_PATH.read_bytes()

    html = alternative.get_body(preferencelist=("html",)).get_content()
    assert f'src="cid:{LOGO_CID}"' in html
    assert "meshloom.app" not in raw
    assert "http://" not in raw and "https://" not in raw
