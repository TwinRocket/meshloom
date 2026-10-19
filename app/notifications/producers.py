"""The producers' entry points to the dispatcher.

Each producer decides whether its occurrence is worth reporting (an outgoing message, a
pending channel, a muted one, a first-seen contact during the radio setup...), builds the
legacy payload dict and hands it to ``emit``: the rules (``rules.py``) choose the channels,
the dispatcher queues, limits, retries and journals. Nothing here talks to a medium.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.notifications import rules as rules_mod
from app.notifications.dispatcher import emit
from app.notifications.events import event_from_payload

logger = logging.getLogger(__name__)

# The block lists were applied to the Apprise medium only (4.18.x); MSHL-113 moves them to
# the rules and every channel.
BLOCKED_SENDER_EXCLUDES = ("legacy:apprise",)


async def emit_message(data: Mapping[str, Any]) -> None:
    """``message.dm`` / ``message.channel`` for an incoming message. Never raises.

    Outgoing messages, messages of a pending channel and of a muted one (unless the event is
    ``always_deliver``) never notify; a blocked sender's message skips the Apprise channel.
    """
    try:
        await _emit_message(data)
    except Exception:
        logger.debug("message notification failed", exc_info=True)


async def _emit_message(data: Mapping[str, Any]) -> None:
    if data.get("outgoing"):
        return
    payload = dict(data)
    payload.setdefault("event", "message")
    event = event_from_payload(payload)
    if event is None:
        return
    msg_type = str(payload.get("type") or "")
    key = str(payload.get("conversation_key") or "")
    muted = False
    is_open = False
    if msg_type == "CHAN" and key:
        from app.channel_constants import is_public_channel_key
        from app.repository.channels import ChannelRepository

        try:
            channel = await ChannelRepository.get_by_key(key)
        except Exception:
            channel = None
            logger.debug("channel unreadable for a notification", exc_info=True)
        if channel is not None and channel.membership == "pending":
            return
        muted = bool(channel is not None and channel.muted)
        is_open = is_public_channel_key(key) or bool(channel is not None and channel.is_hashtag)
    rules = await rules_mod.load_rules()
    if muted and not rules_mod.always_deliver(rules, event.event):
        return
    candidates = rules_mod.event_channel_ids(rules, event, is_public_or_hashtag=is_open)
    if not candidates:
        return

    blocked = False
    if any(c in BLOCKED_SENDER_EXCLUDES for c in candidates):
        # The block lists are read only when a channel they protect could be reached.
        try:
            from app.repository.settings import AppSettingsRepository
            from app.services.message_filters import is_blocked

            blocked = is_blocked(payload, await AppSettingsRepository.get())
        except Exception:
            # Could not read the block lists: fail closed for the medium they protected.
            blocked = True
            logger.debug("block lists unreadable", exc_info=True)
    await emit(
        event,
        payload=payload,
        exclude=BLOCKED_SENDER_EXCLUDES if blocked else (),
        rules=rules,
        is_open=is_open,
    )


async def emit_payload(payload: Mapping[str, Any]) -> None:
    """Any other event, from the producers' payload dict (first seen, channel found,
    telemetry alert, radio watch, update)."""
    event = event_from_payload(payload)
    if event is None:
        logger.debug("no catalogue event for %r", payload.get("event"))
        return
    await emit(event, payload=dict(payload))


async def emit_oss_update(current: str, latest: str) -> bool:
    """``app.update``. True when the event was queued for at least one enabled, unpaused
    channel (the caller then records ``last_notified_update_version``); False when the event
    is off, its channels are disabled or the global pause holds it: it is offered again at the
    next check."""
    payload = {"event": "oss_update", "current": current, "latest": latest}
    event = event_from_payload(payload)
    if event is None:
        return False
    return bool(await emit(event, payload=payload))
