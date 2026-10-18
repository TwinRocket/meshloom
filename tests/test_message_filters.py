"""Block lists and mute for the outputs that forward a message to people (MSHL-104).

The Apprise notification medium and the Apprise fanout skip blocked senders and
muted channels; MQTT, SQS and the webhook fanout still get everything.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.notify as notify
from app.models import AppSettings, NotificationAppriseDest, NotificationDestinations
from app.notifications.dispatcher import notification_dispatcher
from app.repository.channels import ChannelRepository
from app.repository.settings import AppSettingsRepository
from app.services.message_filters import is_blocked, is_filtered

DM_KEY = "aa" * 32
BOB_KEY = "bb" * 32
CHAN_KEY = "CC" * 16
TG = "tgram://111111:AAAA-token/1001"


def _dm(key: str = DM_KEY, sender: str = "Alice") -> dict:
    return {"type": "PRIV", "conversation_key": key, "sender_name": sender, "text": "hi"}


def _chan(sender_key: str | None = BOB_KEY, sender: str = "Bob") -> dict:
    return {
        "type": "CHAN",
        "conversation_key": CHAN_KEY,
        "channel_name": "#ops",
        "sender_key": sender_key,
        "sender_name": sender,
        "text": "hi",
    }


def test_is_blocked_matches_the_messages_listing_rule():
    settings = AppSettings(blocked_keys=[DM_KEY, BOB_KEY], blocked_names=["Spammer"])
    assert is_blocked(_dm(), settings)
    assert is_blocked(_dm(key=DM_KEY.upper()), settings)
    assert is_blocked(_chan(), settings)
    assert is_blocked(_chan(sender_key=None, sender="Spammer"), settings)
    assert not is_blocked(_chan(sender_key="ee" * 32, sender="Carol"), settings)
    assert not is_blocked({**_dm(), "outgoing": True}, settings)


@pytest.mark.asyncio
async def test_muted_channel_is_filtered_until_the_mute_expires(test_db):
    await ChannelRepository.upsert(CHAN_KEY, "#ops")
    assert not await is_filtered(_chan(sender_key=None))
    await ChannelRepository.set_muted(CHAN_KEY, True)
    assert await is_filtered(_chan(sender_key=None))
    await ChannelRepository.set_muted(CHAN_KEY, True, muted_until=int(time.time()) - 5)
    assert not await is_filtered(_chan(sender_key=None))


@pytest.fixture
async def apprise_submissions(test_db, monkeypatch) -> list[dict]:
    submitted: list[dict] = []

    async def fake_send(dest, payload):
        submitted.append(dict(payload))

    monkeypatch.setattr(notify, "send_apprise_alert", fake_send)
    monkeypatch.setattr(notify, "notify_destination_changed", lambda *_: None)
    await AppSettingsRepository.update(
        notification_destinations=NotificationDestinations(apprise=NotificationAppriseDest(urls=TG))
    )
    await AppSettingsRepository.set_push_defaults({"new_dm": {"push": False, "apprise": True}})
    return submitted


@pytest.mark.asyncio
async def test_blocked_sender_never_reaches_the_apprise_medium(apprise_submissions):
    from app.push.manager import push_manager

    await AppSettingsRepository.update(blocked_keys=[DM_KEY])
    await push_manager.dispatch_message(_dm())
    await notification_dispatcher().wait_idle()
    assert apprise_submissions == []

    await AppSettingsRepository.update(blocked_keys=[], blocked_names=["Alice"])
    await push_manager.dispatch_message(_dm())
    await notification_dispatcher().wait_idle()
    assert apprise_submissions == []

    await AppSettingsRepository.update(blocked_names=[])
    await push_manager.dispatch_message(_dm())
    await notification_dispatcher().wait_idle()
    assert len(apprise_submissions) == 1


@pytest.mark.asyncio
async def test_muted_channel_never_reaches_the_apprise_medium(apprise_submissions):
    from app.push.manager import push_manager

    await ChannelRepository.upsert(CHAN_KEY, "#ops")
    await AppSettingsRepository.set_push_conversation_override(
        f"channel-{CHAN_KEY}", {"apprise": True}
    )
    await ChannelRepository.set_muted(CHAN_KEY, True)
    await push_manager.dispatch_message(_chan())
    await notification_dispatcher().wait_idle()
    assert apprise_submissions == []

    await ChannelRepository.set_muted(CHAN_KEY, True, muted_until=int(time.time()) - 5)
    await push_manager.dispatch_message(_chan())
    await notification_dispatcher().wait_idle()
    assert len(apprise_submissions) == 1


@pytest.mark.asyncio
async def test_apprise_fanout_skips_blocked_and_muted(test_db):
    from app.fanout import apprise_mod

    module = apprise_mod.AppriseModule("a", {"urls": TG})
    await ChannelRepository.upsert(CHAN_KEY, "#ops")
    with (
        patch.object(apprise_mod, "_validated_servers", AsyncMock(return_value=[])),
        patch.object(apprise_mod, "_send_sync", return_value=True) as send,
    ):
        await AppSettingsRepository.update(blocked_keys=[BOB_KEY])
        await module.on_message(_chan())
        await AppSettingsRepository.update(blocked_keys=[])
        await ChannelRepository.set_muted(CHAN_KEY, True)
        await module.on_message(_chan())
        send.assert_not_called()

        await ChannelRepository.set_muted(CHAN_KEY, True, muted_until=int(time.time()) - 5)
        await module.on_message(_chan())
        send.assert_called_once()


@pytest.mark.asyncio
async def test_machine_outputs_still_get_blocked_and_muted_messages(test_db):
    """Regression: MQTT, SQS and the webhook fanout forward everything."""
    from app.fanout.mqtt_private import MqttPrivateModule
    from app.fanout.sqs import SqsModule
    from app.fanout.webhook import WebhookModule

    await ChannelRepository.upsert(CHAN_KEY, "#ops")
    await ChannelRepository.set_muted(CHAN_KEY, True)
    await AppSettingsRepository.update(blocked_keys=[BOB_KEY])
    message = _chan()

    webhook = WebhookModule("w", {"url": "http://localhost:9999/hook"})
    await webhook.start()
    sent: list[dict] = []

    async def capture(data: dict, *, event_type: str) -> None:
        sent.append(data)

    webhook._send = capture  # type: ignore[method-assign]
    await webhook.on_message(message)
    await webhook.stop()
    assert sent == [message]

    sqs_client = MagicMock()
    with patch("app.fanout.sqs.boto3.client", return_value=sqs_client):
        sqs = SqsModule("s", {"queue_url": "https://sqs.us-east-1.amazonaws.com/123456789012/mesh"})
        await sqs.start()
        await sqs.on_message(message)
    sqs_client.send_message.assert_called_once()

    mqtt = MqttPrivateModule("m", {"broker_host": "broker.example"})
    publisher = MagicMock()
    publisher.connected = True
    publisher._settings = object()
    publisher.publish = AsyncMock()
    mqtt._publisher = publisher
    await mqtt.on_message(message)
    publisher.publish.assert_awaited_once()
