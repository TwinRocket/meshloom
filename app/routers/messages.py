import logging
import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.event_handlers import track_pending_ack
from app.models import (
    Message,
    MessagesAroundResponse,
    ResendChannelMessageResponse,
    SendChannelMessageRequest,
    SendDirectMessageRequest,
)
from app.repository import AmbiguousPublicKeyPrefixError, AppSettingsRepository, MessageRepository
from app.services.message_send import (
    SCOPE_UNSET,
    resend_channel_message_record,
    send_channel_message_to_channel,
    send_direct_message_to_contact,
)
from app.services.radio_runtime import radio_runtime as radio_manager
from app.websocket import broadcast_error, broadcast_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/messages", tags=["messages"])

_SQLITE_MAX_INT = 2**63 - 1


@router.get("/around/{message_id}", response_model=MessagesAroundResponse)
async def get_messages_around(
    message_id: int,
    type: str | None = Query(default=None, description="Filter by type: PRIV or CHAN"),
    conversation_key: str | None = Query(default=None, description="Filter by conversation key"),
    context: int = Query(default=100, ge=1, le=500, description="Number of messages before/after"),
) -> MessagesAroundResponse:
    """Get messages around a specific message for jump-to-message navigation."""
    settings = await AppSettingsRepository.get()
    blocked_keys = settings.blocked_keys or None
    blocked_names = settings.blocked_names or None
    messages, has_older, has_newer = await MessageRepository.get_around(
        message_id=message_id,
        msg_type=type,
        conversation_key=conversation_key,
        context_size=context,
        blocked_keys=blocked_keys,
        blocked_names=blocked_names,
    )
    return MessagesAroundResponse(messages=messages, has_older=has_older, has_newer=has_newer)


@router.get("", response_model=list[Message])
async def list_messages(
    limit: int = Query(default=100, ge=1, le=1000, description="Page size, 1 to 1000"),
    offset: int = Query(default=0, ge=0, description="Rows to skip; ignored with before/before_id"),
    type: str | None = Query(default=None, description="Filter by type: PRIV or CHAN"),
    conversation_key: str | None = Query(
        default=None, description="Filter by conversation key (channel key or contact pubkey)"
    ),
    before: int | None = Query(
        default=None, description="Cursor: received_at of last seen message"
    ),
    before_id: int | None = Query(default=None, description="Cursor: id of last seen message"),
    after: int | None = Query(
        default=None, description="Forward cursor: received_at of last seen message"
    ),
    after_id: int | None = Query(
        default=None, description="Forward cursor: id of last seen message"
    ),
    q: str | None = Query(default=None, description="Full-text search query"),
    outgoing: bool | None = Query(
        default=None,
        description="true: only sent messages; false: only received ones; absent: both",
    ),
    since_id: int | None = Query(
        default=None,
        description=(
            "Integer between 1 and 2**63-1. Reconciliation cursor: messages with id > since_id, ordered by id ascending "
            "(historical rows included). Cannot be combined with before, after or offset"
        ),
    ),
) -> list[Message]:
    """List messages from the database."""
    if since_id is not None and not 1 <= since_id <= _SQLITE_MAX_INT:
        raise HTTPException(status_code=400, detail="since_id must be an integer >= 1")
    if since_id is not None and (
        before is not None
        or after is not None
        or before_id is not None
        or after_id is not None
        or offset
    ):
        raise HTTPException(
            status_code=400,
            detail="since_id cannot be combined with before, after or offset",
        )
    settings = await AppSettingsRepository.get()
    blocked_keys = settings.blocked_keys or None
    blocked_names = settings.blocked_names or None
    return await MessageRepository.get_all(
        limit=limit,
        offset=offset,
        msg_type=type,
        conversation_key=conversation_key,
        before=before,
        before_id=before_id,
        after=after,
        after_id=after_id,
        q=q,
        blocked_keys=blocked_keys,
        blocked_names=blocked_names,
        outgoing=outgoing,
        since_id=since_id,
    )


@router.post("/direct", response_model=Message)
async def send_direct_message(request: SendDirectMessageRequest) -> Message:
    """Send a direct message to a contact."""
    radio_manager.require_connected()

    # First check our database for the contact
    from app.repository import ContactRepository

    try:
        db_contact = await ContactRepository.get_by_key_or_prefix(request.destination)
    except AmbiguousPublicKeyPrefixError as err:
        sample = ", ".join(key[:12] for key in err.matches[:2])
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ambiguous destination key prefix '{err.prefix}'. "
                f"Use a full 64-character public key. Matching contacts: {sample}"
            ),
        ) from err
    if not db_contact:
        raise HTTPException(
            status_code=404, detail=f"Contact not found in database: {request.destination}"
        )
    if len(db_contact.public_key) < 64:
        raise HTTPException(
            status_code=409,
            detail="Cannot send to an unresolved prefix-only contact until a full key is known",
        )

    result = await send_direct_message_to_contact(
        contact=db_contact,
        text=request.text,
        radio_manager=radio_manager,
        broadcast_fn=broadcast_event,
        track_pending_ack_fn=track_pending_ack,
        now_fn=time.time,
        message_repository=MessageRepository,
        contact_repository=ContactRepository,
    )
    return result.message


# Preferred first radio slot used for sending channel messages.
# The send service may reuse/load other app-managed slots depending on transport
# and session cache state.
TEMP_RADIO_SLOT = 0


@router.post("/channel", response_model=Message)
async def send_channel_message(request: SendChannelMessageRequest) -> Message:
    """Send a message to a channel."""
    from app.repository import ChannelRepository
    from app.services.channel_membership import is_pending_channel

    db_channel = await ChannelRepository.get_by_key(request.channel_key)
    if is_pending_channel(db_channel):
        raise HTTPException(
            status_code=409,
            detail="Pending channels cannot be sent to until they are adopted",
        )

    radio_manager.require_connected()

    if not db_channel:
        raise HTTPException(
            status_code=404, detail=f"Channel {request.channel_key} not found in database"
        )

    # Convert channel key hex to bytes
    try:
        key_bytes = bytes.fromhex(request.channel_key)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Invalid channel key format: {request.channel_key}"
        ) from None

    # None field = no per-send override (fall back to the channel's persisted
    # override). An explicit string (including "" for unscoped) overrides it.
    flood_scope_override = (
        SCOPE_UNSET if request.flood_scope_override is None else request.flood_scope_override
    )

    return await send_channel_message_to_channel(
        channel=db_channel,
        channel_key_upper=request.channel_key.upper(),
        key_bytes=key_bytes,
        text=request.text,
        radio_manager=radio_manager,
        broadcast_fn=broadcast_event,
        error_broadcast_fn=broadcast_error,
        now_fn=time.time,
        temp_radio_slot=TEMP_RADIO_SLOT,
        flood_scope_override=flood_scope_override,
        message_repository=MessageRepository,
    )


RESEND_WINDOW_SECONDS = 30
# Upper bound on the server's own clock when the caller supplies its own age.
# The browser is authoritative for the 30 s window (its clock may disagree with
# the server's), but a byte-perfect resend of a stale packet would re-flood an
# old message once repeaters have forgotten its hash, so a client with a broken
# clock or anchor must not be able to resend an arbitrarily old message.
RESEND_CLIENT_AGE_MAX_SERVER_ELAPSED_SECONDS = 300


def _resend_window_expired_detail(
    seconds: int = RESEND_WINDOW_SECONDS, *, on_server: bool = False
) -> dict:
    """Coded 400 detail, translated by the UI under errors.<code>."""
    if on_server:
        return {
            "code": "resend_window_expired_server",
            "message": (
                f"Resend window has expired (message older than {seconds} seconds on the server)"
            ),
            "params": {"seconds": seconds},
        }
    return {
        "code": "resend_window_expired",
        "message": f"Resend window has expired ({seconds} seconds)",
        "params": {"seconds": seconds},
    }


@router.post(
    "/channel/{message_id}/resend",
    response_model=ResendChannelMessageResponse,
    response_model_exclude_none=True,
)
async def resend_channel_message(
    message_id: int,
    new_timestamp: bool = Query(default=False),
    client_age_seconds: Annotated[
        float | None,
        Query(
            ge=0,
            description=(
                "Age of the message measured by the caller's own clock, in seconds. "
                "When set, the 30 s byte-perfect window is checked against this value "
                "instead of the server clock (the server only refuses messages older "
                "than 300 s on its own clock). Ignored when new_timestamp=true."
            ),
        ),
    ] = None,
) -> ResendChannelMessageResponse:
    """Resend a channel message.

    When new_timestamp=False (default): byte-perfect resend using the original timestamp.
    Only allowed within 30 seconds of the original send. Without client_age_seconds the
    age is measured on the server clock. With client_age_seconds (sent by the web UI,
    whose clock is authoritative for this window) the 30 s check uses that value, and
    the server clock only enforces a 300 s safety bound.

    When new_timestamp=True: resend with a fresh timestamp so repeaters treat it as a
    new packet. Creates a new message row in the database. No time window restriction.
    """
    radio_manager.require_connected()

    from app.repository import ChannelRepository

    msg = await MessageRepository.get_by_id(message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    if not msg.outgoing:
        raise HTTPException(status_code=400, detail="Can only resend outgoing messages")

    if msg.type != "CHAN":
        raise HTTPException(status_code=400, detail="Can only resend channel messages")

    if msg.sender_timestamp is None:
        raise HTTPException(status_code=400, detail="Message has no timestamp")

    # Byte-perfect resend enforces the 30s window; new-timestamp resend does not.
    # The caller's own age, when given, is authoritative (see client_age_seconds).
    if not new_timestamp:
        elapsed = int(time.time()) - msg.sender_timestamp
        age = elapsed if client_age_seconds is None else client_age_seconds
        if age > RESEND_WINDOW_SECONDS:
            raise HTTPException(status_code=400, detail=_resend_window_expired_detail())
        if (
            client_age_seconds is not None
            and elapsed > RESEND_CLIENT_AGE_MAX_SERVER_ELAPSED_SECONDS
        ):
            raise HTTPException(
                status_code=400,
                detail=_resend_window_expired_detail(
                    RESEND_CLIENT_AGE_MAX_SERVER_ELAPSED_SECONDS, on_server=True
                ),
            )

    db_channel = await ChannelRepository.get_by_key(msg.conversation_key)
    if not db_channel:
        raise HTTPException(status_code=404, detail=f"Channel {msg.conversation_key} not found")

    return await resend_channel_message_record(
        message=msg,
        channel=db_channel,
        new_timestamp=new_timestamp,
        radio_manager=radio_manager,
        broadcast_fn=broadcast_event,
        error_broadcast_fn=broadcast_error,
        now_fn=time.time,
        temp_radio_slot=TEMP_RADIO_SLOT,
        message_repository=MessageRepository,
    )


@router.delete("/{message_id}")
async def delete_message(message_id: int) -> dict[str, str]:
    """Remove a stored message from this server. Does not retract it from the mesh."""
    existing = await MessageRepository.get_by_id(message_id)
    if existing is None:
        return {"status": "ok"}
    await MessageRepository.delete_by_id(message_id)
    broadcast_event("message_deleted", {"message_id": message_id})
    return {"status": "ok"}
