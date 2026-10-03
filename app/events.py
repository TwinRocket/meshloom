"""Typed WebSocket event contracts and serialization helpers."""

import json
import logging
from typing import Any, Literal, NotRequired

from pydantic import TypeAdapter
from typing_extensions import TypedDict

from app.models import (
    Channel,
    CommunityLiveStatus,
    CommunityPacketBroadcast,
    Contact,
    Message,
    MessagePath,
    RadioStatusResponse,
    RawPacketBroadcast,
)
from app.routers.health import HealthResponse

logger = logging.getLogger(__name__)

WsEventType = Literal[
    "health",
    "message",
    "contact",
    "contact_resolved",
    "channel",
    "contact_deleted",
    "channel_deleted",
    "raw_packet",
    "community_packet",
    "community_live",
    "message_acked",
    "message_deleted",
    "error",
    "success",
    "radio_created",
    "radio_updated",
    "radio_deleted",
]


class ContactDeletedPayload(TypedDict):
    public_key: str


class ContactResolvedPayload(TypedDict):
    previous_public_key: str
    contact: Contact


class ChannelDeletedPayload(TypedDict):
    key: str


class RadioDeletedPayload(TypedDict):
    radio_id: str


class MessageAckedPayload(TypedDict):
    message_id: int
    ack_count: int
    paths: NotRequired[list[MessagePath]]
    packet_id: NotRequired[int | None]
    packet_hash: NotRequired[str | None]
    observer_reach_eligible: NotRequired[bool | None]


class MessageDeletedPayload(TypedDict):
    message_id: int


class ToastPayload(TypedDict):
    message: str
    details: NotRequired[str]
    code: NotRequired[str]
    params: NotRequired[dict[str, Any]]


_PAYLOAD_ADAPTERS: dict[WsEventType, TypeAdapter[Any]] = {
    "health": TypeAdapter(HealthResponse),
    "message": TypeAdapter(Message),
    "contact": TypeAdapter(Contact),
    "contact_resolved": TypeAdapter(ContactResolvedPayload),
    "channel": TypeAdapter(Channel),
    "contact_deleted": TypeAdapter(ContactDeletedPayload),
    "channel_deleted": TypeAdapter(ChannelDeletedPayload),
    "raw_packet": TypeAdapter(RawPacketBroadcast),
    "community_packet": TypeAdapter(CommunityPacketBroadcast),
    "community_live": TypeAdapter(CommunityLiveStatus),
    "message_acked": TypeAdapter(MessageAckedPayload),
    "message_deleted": TypeAdapter(MessageDeletedPayload),
    "error": TypeAdapter(ToastPayload),
    "success": TypeAdapter(ToastPayload),
    "radio_created": TypeAdapter(RadioStatusResponse),
    "radio_updated": TypeAdapter(RadioStatusResponse),
    "radio_deleted": TypeAdapter(RadioDeletedPayload),
}


def dump_ws_event(event_type: str, data: Any, radio_id: str | None = None) -> str:
    """Serialize a WebSocket event envelope with validation for known event types."""
    effective_radio_id = radio_id
    if effective_radio_id is None:
        if isinstance(data, dict) and "radio_id" in data and data["radio_id"]:
            effective_radio_id = str(data["radio_id"])
        elif hasattr(data, "radio_id") and getattr(data, "radio_id", None):
            effective_radio_id = str(data.radio_id)

    adapter = _PAYLOAD_ADAPTERS.get(event_type)  # type: ignore[arg-type]
    if adapter is None:
        envelope: dict[str, Any] = {"type": event_type}
        if effective_radio_id is not None:
            envelope["radio_id"] = effective_radio_id
        envelope["data"] = data
        return json.dumps(envelope)

    try:
        validated = adapter.validate_python(data)
        # Omit absent packet_hash on community rain so old-Stats frames stay hash8-only.
        payload = adapter.dump_python(
            validated, mode="json", exclude_none=(event_type == "community_packet")
        )
        if effective_radio_id is not None and isinstance(payload, dict) and "radio_id" not in payload:
            payload["radio_id"] = effective_radio_id
        envelope = {"type": event_type}
        if effective_radio_id is not None:
            envelope["radio_id"] = effective_radio_id
        envelope["data"] = payload
        return json.dumps(envelope)
    except Exception:
        logger.exception(
            "Failed to validate WebSocket payload for event %s; falling back to raw JSON envelope",
            event_type,
        )
        envelope = {"type": event_type}
        if effective_radio_id is not None:
            envelope["radio_id"] = effective_radio_id
        envelope["data"] = data
        return json.dumps(envelope)


