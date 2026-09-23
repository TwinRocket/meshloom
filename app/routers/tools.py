"""Mesh diagnostic tools that send without storing anything locally."""

import logging
import time

from fastapi import APIRouter, HTTPException
from meshcore import EventType
from pydantic import BaseModel, Field

from app.decoder import outgoing_group_text_packet_hash
from app.models import Channel
from app.region_scope import normalize_region_scope
from app.repository import AppSettingsRepository, MessageRepository
from app.services.directory import directory_is_available
from app.services.message_send import (
    NO_RADIO_RESPONSE_AFTER_SEND_DETAIL,
    allocate_outgoing_sender_timestamp,
    release_outgoing_sender_timestamp,
    send_channel_message_with_effective_scope,
)
from app.services.observer_reach import local_radio_origin
from app.services.radio_runtime import radio_runtime as radio_manager
from app.services.test_channel import (
    TEST_CHANNEL_KEY,
    TEST_CHANNEL_KEY_HEX,
    TEST_CHANNEL_NAME,
)
from app.websocket import broadcast_error

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tools", tags=["tools"])

# Radio slot borrowed for the test send. It is rewritten empty in a finally so
# the test channel's name and secret never stay resident on the radio.
MESH_TEST_RADIO_SLOT = 0
MESH_TEST_BODY_PREFIX = "meshloom-test"


class MeshTestRequest(BaseModel):
    flood_scope: str = Field(
        description="Region the test packet is scoped to. Must be a known region."
    )


class MeshTestResponse(BaseModel):
    packet_hash: str
    sent_at: int
    flood_scope: str
    origin_lat: float | None = None
    origin_lon: float | None = None


def _test_channel_stub() -> Channel:
    """In-memory channel for the send helper. Never stored, never on the radio."""
    return Channel(key=TEST_CHANNEL_KEY_HEX, name=TEST_CHANNEL_NAME, is_hashtag=True)


async def _resolve_known_region(requested: str) -> str:
    """Return the stored region matching ``requested``, or raise 400.

    Comparison happens after the same normalization the radio flood-scope code
    applies, so ``Esperance`` and ``#Esperance`` are the same region. Unscoped
    is not a region: it only passes when the operator listed that exact token.
    """
    settings = await AppSettingsRepository.get()
    normalized = normalize_region_scope(requested)
    for region in settings.known_regions:
        if normalize_region_scope(region) == normalized:
            return region
    raise HTTPException(status_code=400, detail=f"Unknown region: {requested!r}")


async def _clear_test_slot(mc) -> None:
    """Blank the borrowed slot. Retried so a single radio glitch does not leave it resident."""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            await mc.commands.set_channel(
                channel_idx=MESH_TEST_RADIO_SLOT,
                channel_name="",
                channel_secret=bytes(16),
            )
            return
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Attempt %d/3: failed to clear radio slot %d after the mesh test",
                attempt + 1,
                MESH_TEST_RADIO_SLOT,
                exc_info=True,
            )
    logger.error(
        "Radio slot %d still holds the mesh test channel after 3 clear attempts: %s",
        MESH_TEST_RADIO_SLOT,
        last_error,
    )


@router.post("/mesh-test", response_model=MeshTestResponse)
async def send_mesh_test(request: MeshTestRequest) -> MeshTestResponse:
    """Send one scoped GroupText on the built-in test channel and report its hash.

    Nothing is persisted: no Channel row, no Message row, no send-slot cache
    entry. The packet is only observable through Community reach, which is why
    the endpoint is unavailable when Community is off.
    """
    if not await directory_is_available():
        raise HTTPException(
            status_code=404,
            detail="The mesh test needs Meshloom Community to collect observations",
        )

    region = await _resolve_known_region(request.flood_scope)
    radio_manager.require_connected()

    # Free the borrowed slot in the send cache before the radio sees it, so a
    # channel that used to live there is reloaded on its next send.
    for cached_key, cached_slot in radio_manager.get_channel_send_cache_snapshot():
        if cached_slot == MESH_TEST_RADIO_SLOT:
            radio_manager.invalidate_cached_channel_slot(cached_key)

    sent_at = int(time.time())
    body = f"{MESH_TEST_BODY_PREFIX} {sent_at}"
    sender_timestamp: int | None = None
    text_with_sender = body

    try:
        async with radio_manager.radio_operation("send_mesh_test", pause_polling=True) as mc:
            radio_name = mc.self_info.get("name", "") if mc.self_info else ""
            text_with_sender = f"{radio_name}: {body}" if radio_name else body
            # No Message row is written, so this only keeps two tests fired in the
            # same second from producing one indistinguishable packet hash.
            sender_timestamp = await allocate_outgoing_sender_timestamp(
                message_repository=MessageRepository,
                msg_type="CHAN",
                conversation_key=TEST_CHANNEL_KEY_HEX,
                text=text_with_sender,
                requested_timestamp=sent_at,
            )
            logger.info("Sending mesh test scoped to %s", region)

            try:
                result = await send_channel_message_with_effective_scope(
                    mc=mc,
                    channel=_test_channel_stub(),
                    channel_key=TEST_CHANNEL_KEY_HEX,
                    key_bytes=TEST_CHANNEL_KEY,
                    text=body,
                    timestamp_bytes=sender_timestamp.to_bytes(4, "little"),
                    action_label="mesh test",
                    radio_manager=radio_manager,
                    temp_radio_slot=MESH_TEST_RADIO_SLOT,
                    error_broadcast_fn=broadcast_error,
                    flood_scope_override=region,
                    register_slot=False,
                )
            finally:
                await _clear_test_slot(mc)

            if result is None:
                logger.warning("No response from radio after the mesh test; outcome is unknown")
                raise HTTPException(status_code=422, detail=NO_RADIO_RESPONSE_AFTER_SEND_DETAIL)
            if result.type == EventType.ERROR:
                raise HTTPException(
                    status_code=422, detail=f"Failed to send mesh test: {result.payload}"
                )
    finally:
        if sender_timestamp is not None:
            await release_outgoing_sender_timestamp(
                msg_type="CHAN",
                conversation_key=TEST_CHANNEL_KEY_HEX,
                text=text_with_sender,
                sender_timestamp=sender_timestamp,
            )

    if sender_timestamp is None:
        raise HTTPException(status_code=422, detail="Failed to allocate a mesh test timestamp")

    # The firmware prefixes the radio name before encrypting, so the hash has to
    # be computed over the same "Name: body" string an observer will report.
    packet_hash = outgoing_group_text_packet_hash(
        TEST_CHANNEL_KEY_HEX, sender_timestamp, text_with_sender
    )
    if packet_hash is None:
        raise HTTPException(status_code=422, detail="Mesh test packet hash is not reproducible")

    origin = local_radio_origin()
    return MeshTestResponse(
        packet_hash=packet_hash,
        sent_at=sent_at,
        flood_scope=region,
        origin_lat=origin[0] if origin is not None else None,
        origin_lon=origin[1] if origin is not None else None,
    )
