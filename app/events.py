"""Typed WebSocket event contracts and serialization helpers."""

import json
import logging
from typing import Any, NotRequired

from pydantic import TypeAdapter
from typing_extensions import TypedDict

from app.auth.ws_events import WsEvent
from app.models import (
    Channel,
    CommunityLiveStatus,
    CommunityPacketBroadcast,
    Contact,
    Message,
    MessagePath,
    RadioWifiState,
    RawPacketBroadcast,
)
from app.routers.health import HealthResponse

logger = logging.getLogger(__name__)


class ContactDeletedPayload(TypedDict):
    public_key: str


class ContactResolvedPayload(TypedDict):
    previous_public_key: str
    contact: Contact


class ChannelDeletedPayload(TypedDict):
    key: str


class MessageAckedPayload(TypedDict):
    message_id: int
    ack_count: int
    paths: NotRequired[list[MessagePath]]
    packet_id: NotRequired[int | None]
    packet_hash: NotRequired[str | None]
    observer_reach_eligible: NotRequired[bool | None]


class MessageDeletedPayload(TypedDict):
    message_id: int


class PluginStatusPayload(TypedDict):
    """A plugin instance's status change (`app/plugins/host.py`)."""

    instance_id: str
    plugin_id: str
    label: str
    status: str
    last_error: str | None
    dropped_events: int
    restarts: int
    summary: NotRequired[str | None]
    send_guard: NotRequired[dict[str, Any]]
    attention: NotRequired[dict[str, Any] | None]


class ToastPayload(TypedDict):
    message: str
    details: NotRequired[str]
    code: NotRequired[str]
    params: NotRequired[dict[str, Any]]


# One payload model per member (``tests/test_websocket.py`` checks they match).
_PAYLOAD_ADAPTERS: dict[WsEvent, TypeAdapter[Any]] = {
    WsEvent.HEALTH: TypeAdapter(HealthResponse),
    WsEvent.MESSAGE: TypeAdapter(Message),
    WsEvent.CONTACT: TypeAdapter(Contact),
    WsEvent.CONTACT_RESOLVED: TypeAdapter(ContactResolvedPayload),
    WsEvent.CHANNEL: TypeAdapter(Channel),
    WsEvent.CONTACT_DELETED: TypeAdapter(ContactDeletedPayload),
    WsEvent.CHANNEL_DELETED: TypeAdapter(ChannelDeletedPayload),
    WsEvent.RAW_PACKET: TypeAdapter(RawPacketBroadcast),
    WsEvent.COMMUNITY_PACKET: TypeAdapter(CommunityPacketBroadcast),
    WsEvent.COMMUNITY_LIVE: TypeAdapter(CommunityLiveStatus),
    WsEvent.MESSAGE_ACKED: TypeAdapter(MessageAckedPayload),
    WsEvent.MESSAGE_DELETED: TypeAdapter(MessageDeletedPayload),
    WsEvent.RADIO_WIFI: TypeAdapter(RadioWifiState),
    WsEvent.PLUGIN_STATUS: TypeAdapter(PluginStatusPayload),
    WsEvent.ERROR: TypeAdapter(ToastPayload),
    WsEvent.SUCCESS: TypeAdapter(ToastPayload),
}


def dump_ws_event(event_type: WsEvent, data: Any) -> str:
    """Serialize a WebSocket event envelope, its payload validated by the type's model."""
    adapter = _PAYLOAD_ADAPTERS.get(event_type)
    if adapter is None:
        return json.dumps({"type": event_type, "data": data})

    try:
        validated = adapter.validate_python(data)
        # Omit absent packet_hash on community rain so old-Stats frames stay hash8-only.
        payload = adapter.dump_python(
            validated, mode="json", exclude_none=(event_type == WsEvent.COMMUNITY_PACKET)
        )
        return json.dumps({"type": event_type, "data": payload})
    except Exception:
        logger.exception(
            "Failed to validate WebSocket payload for event %s; falling back to raw JSON envelope",
            event_type,
        )
        return json.dumps({"type": event_type, "data": data})
