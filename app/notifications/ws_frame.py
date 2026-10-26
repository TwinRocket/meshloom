"""The ``notification_event`` WebSocket frame (MSHL-138), for the Feed.

The dispatcher (``dispatcher.py``) broadcasts one frame per catalogue event that matched
at least one rule: an enabled channel is left once the rules, the mutes, the block lists
and the producer's exclusions have run, before the pauses and quiet hours (they hold a
delivery, not the event). An event no rule gives a channel (an event turned off, a muted
conversation, a blocked sender) sends none, and neither do the dispatcher's summaries,
the per-channel heartbeat (``heartbeat.py``) and test sends, which do not go through the
rules. ``rules.emit_ws_events`` (default true) turns the frame off::

    {"version": 1, "event": "message.channel", "event_id": "...", "occurred_at": 1700000000,
     "dedup_key": "...", "urgent": false, "conversation_key": "...",
     "conversation_type": "CHAN", "conversation_open": true, "data": {...typed per event...}}

``data`` is the webhook v2 ``data`` (``webhook_v2.event_data``) with the message text kept:
typed fields, never rendered text. ``conversation_type`` is ``PRIV`` or ``CHAN`` for a
message (``CHAN`` for ``channel.found``), null otherwise; ``conversation_open`` says whether
a ``CHAN`` message is in a Public or ``#`` channel. Who gets the frame is decided per socket
by ``app/websocket.py``: ``notify.*`` events reach admins only, and a direct conversation
(``message.dm``, a ``PRIV`` conversation, a mention on a private-key channel) reaches only
the sockets with ``read_private`` (never a kiosk). A re-emitted event (same ``dedup_key``)
may arrive twice: the consumer dedups.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.notifications import events as ev

logger = logging.getLogger(__name__)

_MESSAGE_EVENTS = (ev.MessageEvent, ev.MentionEvent, ev.KeywordEvent)


def enabled(rules: Mapping[str, Any]) -> bool:
    """``rules.emit_ws_events``: on unless set to ``false``."""
    return rules.get("emit_ws_events", True) is not False


def build_frame(
    event: ev.NotificationEvent, payload: Mapping[str, Any], *, is_open: bool
) -> dict[str, Any]:
    """The frame's payload for ``event`` (``payload`` is its legacy dict)."""
    from app.notifications.webhook_v2 import event_data

    conversation_type: str | None = None
    conversation_open: bool | None = None
    if isinstance(event, _MESSAGE_EVENTS):
        conversation_type = "CHAN" if event.data.type == "CHAN" else "PRIV"
        if conversation_type == "CHAN":
            conversation_open = bool(is_open)
    elif isinstance(event, ev.ChannelFoundEvent):
        conversation_type = "CHAN"
    return {
        "version": event.version,
        "event": event.event,
        "event_id": event.event_id,
        "occurred_at": event.occurred_at,
        "dedup_key": event.dedup_key,
        "urgent": bool(event.urgent),
        "conversation_key": event.conversation_key or None,
        "conversation_type": conversation_type,
        "conversation_open": conversation_open,
        "data": event_data(event, payload, "always").model_dump(mode="json"),
    }


def publish(
    event: ev.NotificationEvent,
    payload: Mapping[str, Any],
    *,
    is_open: bool,
    rules: Mapping[str, Any],
) -> None:
    """Queue the frame for every allowed socket, unless ``rules.emit_ws_events`` is off.
    Never raises, never waits."""
    if not enabled(rules):
        return
    try:
        from app.auth.ws_events import WsEvent
        from app.websocket import ws_manager

        ws_manager.broadcast_nowait(
            WsEvent.NOTIFICATION_EVENT, build_frame(event, payload, is_open=is_open)
        )
    except Exception:
        logger.debug("notification_event frame not sent", exc_info=True)
