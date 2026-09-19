from unittest.mock import AsyncMock

import pytest

from app.models import NotificationDestinations, NotificationWebhookDest
from app.notify import send_webhook_alert
from app.repository.settings import coerce_notification_media, or_notification_media


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
