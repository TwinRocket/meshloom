"""Block lists and channel mute for the outputs that forward a message to people.

The Apprise notification medium and the Apprise fanout skip a message from a
blocked sender (``blocked_keys``, ``blocked_names``: the rule of the messages
listing, ``MessageRepository._build_blocked_incoming_clause``) or on a muted
channel. Machine integrations (MQTT, SQS, webhook fanout) still get everything.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.models import AppSettings


def is_blocked(message: Mapping[str, Any], settings: AppSettings) -> bool:
    """Whether an incoming message comes from a blocked key or name."""
    if message.get("outgoing"):
        return False
    blocked_keys = {key.lower() for key in settings.blocked_keys}
    if blocked_keys:
        msg_type = message.get("type")
        if msg_type == "PRIV":
            sender = str(message.get("conversation_key") or "")
        else:
            sender = str(message.get("sender_key") or "")
        if sender and sender.lower() in blocked_keys:
            return True
    sender_name = message.get("sender_name")
    return bool(sender_name) and sender_name in settings.blocked_names


async def is_filtered(message: Mapping[str, Any]) -> bool:
    """``is_blocked``, or a channel message on a muted channel (mute expiry applied)."""
    from app.repository.channels import ChannelRepository
    from app.repository.settings import AppSettingsRepository

    if is_blocked(message, await AppSettingsRepository.get()):
        return True
    key = str(message.get("conversation_key") or "")
    if message.get("type") != "CHAN" or not key:
        return False
    channel = await ChannelRepository.get_by_key(key)
    return channel is not None and channel.muted
