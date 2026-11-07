"""Block lists and channel mute in the rules engine, and in the Apprise fanout (MSHL-113).

The engine cuts a blocked sender's and a muted channel's ``message.*`` events for every
notification channel; the Apprise fanout (outside the engine) makes the same decision through
``message_filters.is_filtered``; MQTT, SQS and webhook fanout outputs still get everything.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.notifications import events as ev
from app.notifications.rules import event_channel_ids, message_blocked
from app.repository.channels import ChannelRepository
from app.repository.settings import AppSettingsRepository

DM_KEY = "aa" * 32
BOB_KEY = "bb" * 32
CHAN_KEY = "CC" * 16
CHANNELS = ["builtin:push", "legacy:email", "legacy:apprise"]
RULES = {
    "version": 1,
    "paused_until": None,
    "events": {
        "message.dm": {"channels": CHANNELS, "always_deliver": False},
        "message.channel": {"channels": CHANNELS, "always_deliver": False},
        "app.update": {"channels": CHANNELS},
    },
    "conversations": {},
}


def _dm(**extra) -> dict:
    return {
        "event": "message",
        "type": "PRIV",
        "conversation_key": DM_KEY,
        "sender_key": DM_KEY,
        "sender_name": "Alice",
        "text": "hi",
        **extra,
    }


def _chan(**extra) -> dict:
    return {
        "event": "message",
        "type": "CHAN",
        "conversation_key": CHAN_KEY,
        "channel_name": "#ops",
        "sender_key": BOB_KEY,
        "sender_name": "Bob",
        "text": "hi",
        **extra,
    }


def _event(payload: dict) -> ev.NotificationEvent:
    event = ev.event_from_payload(payload)
    assert event is not None
    return event


# ── The engine ───────────────────────────────────────────────────────────


def test_blocked_sender_reaches_no_channel_unless_always_deliver():
    dm = _event(_dm())
    assert event_channel_ids(RULES, dm) == CHANNELS
    assert event_channel_ids(RULES, dm, blocked=True) == []
    always = {
        **RULES,
        "events": {**RULES["events"], "message.dm": {"channels": CHANNELS, "always_deliver": True}},
    }
    assert event_channel_ids(always, dm, blocked=True) == CHANNELS


def test_muted_channel_reaches_no_channel_even_through_an_exception():
    chan = _event(_chan())
    rules = {**RULES, "conversations": {f"channel-{CHAN_KEY}": {"legacy:apprise": True}}}
    assert event_channel_ids(rules, chan, is_public_or_hashtag=True) == CHANNELS
    assert event_channel_ids(rules, chan, is_public_or_hashtag=True, muted=True) == []


def test_mute_and_blocks_only_concern_messages():
    update = _event({"event": "oss_update", "current": "1.0.0", "latest": "1.1.0"})
    assert event_channel_ids(RULES, update, muted=True, blocked=True) == CHANNELS


@pytest.mark.asyncio
async def test_message_blocked_uses_the_shared_rule(test_db):
    await AppSettingsRepository.update(blocked_keys=[DM_KEY.upper()], blocked_names=["Spammer"])
    assert await message_blocked(_dm())
    assert await message_blocked(_dm(conversation_key=DM_KEY.upper()))
    assert await message_blocked(_chan(sender_key=BOB_KEY, sender_name="Spammer"))
    assert await message_blocked(_chan(sender_key=DM_KEY))
    assert not await message_blocked(_chan())
    assert not await message_blocked(_dm(outgoing=True))


# ── The Apprise fanout (outside the engine) ──────────────────────────────


async def _apprise_sends(message: dict) -> int:
    from app.fanout.apprise_mod import AppriseModule

    mod = AppriseModule("test", {"urls": "json://localhost", "allow_private_network": True})
    with patch("app.fanout.apprise_mod._send_sync", return_value=True) as send:
        await mod.on_message(message)
    return send.call_count


@pytest.mark.asyncio
async def test_fanout_apprise_skips_blocked_key_and_name(test_db):
    assert await _apprise_sends(_dm()) == 1
    await AppSettingsRepository.update(blocked_keys=[DM_KEY])
    assert await _apprise_sends(_dm()) == 0
    assert await _apprise_sends(_chan(sender_key=BOB_KEY)) == 1
    await AppSettingsRepository.update(blocked_names=["Bob"])
    assert await _apprise_sends(_chan()) == 0


@pytest.mark.asyncio
async def test_fanout_apprise_follows_the_mute_until_it_expires(test_db):
    await ChannelRepository.upsert(CHAN_KEY, "#ops")
    assert await _apprise_sends(_chan()) == 1
    await ChannelRepository.set_muted(CHAN_KEY, True, muted_until=int(time.time()) + 600)
    assert await _apprise_sends(_chan()) == 0
    await ChannelRepository.set_muted(CHAN_KEY, True)
    assert await _apprise_sends(_chan()) == 0
    await ChannelRepository.set_muted(CHAN_KEY, True, muted_until=int(time.time()) - 5)
    assert await _apprise_sends(_chan()) == 1


@pytest.mark.asyncio
async def test_machine_outputs_still_get_blocked_and_muted_messages(test_db):
    from app.fanout.webhook import WebhookModule

    await ChannelRepository.upsert(CHAN_KEY, "#ops")
    await ChannelRepository.set_muted(CHAN_KEY, True)
    await AppSettingsRepository.update(blocked_keys=[BOB_KEY])
    mod = WebhookModule("test", {"url": "http://localhost:9999/hook"})
    await mod.start()
    sent: list[dict] = []

    async def capture(data: dict, *, event_type: str) -> None:
        sent.append(data)

    mod._send = capture
    await mod.on_message(_chan())
    await mod.stop()
    assert len(sent) == 1


def test_only_the_apprise_fanout_applies_the_filters():
    fanout = Path(__file__).resolve().parents[1] / "app" / "fanout"
    users = {p.name for p in fanout.glob("*.py") if "message_filters" in p.read_text("utf-8")}
    assert users == {"apprise_mod.py"}  # mqtt_private, sqs and webhook are unchanged
