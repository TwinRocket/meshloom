"""End to end against a real ntfy server (``binwiederhier/ntfy``).

Skipped unless ``MESHLOOM_NTFY_URL`` names a running server (a developer-only
variable like the radio bench's; ``scripts/quality/docker_ci.sh --ntfy`` starts a
container and sets it, and so does the ``ntfy-integration`` CI job). The channel is
created on the private address with ``allow_private_network``, an event goes through
the dispatcher (rules not involved: ``targets``), the guard, the retry policy and the
transport, and the published message is read back from ntfy's own JSON API.
"""

from __future__ import annotations

import json
import os
import secrets as stdlib_secrets

import httpx
import pytest

from app.notifications.channels import ChannelCreate, generate_ntfy_topic
from app.notifications.dispatcher import notification_dispatcher
from app.repository.notification_channels import NotificationChannelRepository as Channels

SERVER = os.environ.get("MESHLOOM_NTFY_URL", "")

pytestmark = pytest.mark.skipif(not SERVER, reason="MESHLOOM_NTFY_URL is not set")

CLICK_BASE = "https://meshloom.example.net/"


def _published(topic: str) -> list[dict]:
    response = httpx.get(f"{SERVER.rstrip('/')}/{topic}/json?poll=1&since=all", timeout=10)
    response.raise_for_status()
    messages = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    return [m for m in messages if m.get("event") == "message"]


async def _channel(topic: str, **config):
    return await Channels.create(
        ChannelCreate(
            type="ntfy",
            name="CI ntfy",
            config={
                "server": SERVER,
                "topic": topic,
                "click_url": CLICK_BASE,
                "tags": "mesh",
                **config,
            },
            allow_private_network=True,
            verify_tls=True,
        )
    )


@pytest.mark.asyncio
async def test_radio_offline_reaches_ntfy_with_title_priority_and_click(test_db):
    topic = generate_ntfy_topic()
    channel = await _channel(topic)
    queued = await notification_dispatcher().emit(
        None, payload={"event": "radio_offline"}, targets=[channel.id]
    )
    assert queued == 1
    (message,) = _published(topic)
    assert message["priority"] == 5
    assert message["title"]
    assert message["tags"] == ["mesh"]
    assert message["click"].startswith(CLICK_BASE)
    stored = await Channels.get(channel.id)
    assert stored is not None
    assert stored.failure_count == 0 and stored.last_error is None


@pytest.mark.asyncio
async def test_a_french_title_with_accents_survives_the_json_mode(test_db):
    topic = generate_ntfy_topic()
    channel = await _channel(topic, markdown=False)
    payload = {
        "event": "message",
        "type": "PRIV",
        "conversation_key": "ab" * 32,
        "text": "à demain, déjà prêt ✓",
        "sender_name": "Léa",
        "id": 1,
    }
    await notification_dispatcher().emit(None, payload=payload, targets=[channel.id])
    (message,) = _published(topic)
    assert message["priority"] == 3
    assert "Léa" in message["title"] + message.get("message", "")
    assert message["click"] == f"{CLICK_BASE}#contact/{'ab' * 32}"


@pytest.mark.asyncio
async def test_an_unused_token_and_the_topic_never_reach_the_recorded_state(test_db):
    """The CI server has no access control, so it accepts any token (the 401/403 mapping is
    pinned by ``tests/test_transport_ntfy.py``); the channel's state still carries no secret."""
    topic = generate_ntfy_topic()
    token = "tk_" + stdlib_secrets.token_hex(8)
    channel = await _channel(topic, token=token)
    await notification_dispatcher().emit(
        None, payload={"event": "radio_offline"}, targets=[channel.id]
    )
    stored = await Channels.get(channel.id)
    assert stored is not None
    assert token not in (stored.last_error or "")
    assert topic not in (stored.last_error or "")
