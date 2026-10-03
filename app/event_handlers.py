import logging
import time
from typing import TYPE_CHECKING, Any

from meshcore import EventType

from app.models import CONTACT_TYPE_ROOM, Contact, ContactUpsert
from app.packet_processor import process_raw_packet
from app.repository import (
    ContactRepository,
)
from app.services import dm_ack_tracker
from app.services.contact_reconciliation import (
    promote_prefix_contacts_for_contact,
    record_contact_name_and_reconcile,
)
from app.services.dm_ack_apply import apply_dm_ack_code
from app.services.dm_ingest import (
    ingest_fallback_direct_message,
    resolve_direct_message_sender_metadata,
    resolve_fallback_direct_message_context,
)
from app.services.radio_ingest_gate import ingest_allowed
from app.websocket import broadcast_event

if TYPE_CHECKING:
    from meshcore.events import Event, Subscription

logger = logging.getLogger(__name__)

# Track active subscriptions so we can unsubscribe before re-registering
# This prevents handler duplication after reconnects
_active_subscriptions: list["Subscription"] = []


def _is_ingest_allowed(radio_id: str = "default") -> bool:
    """Check ingest gate for the specified radio."""
    if radio_id == "default":
        return ingest_allowed()
    try:
        from app.services.radio_registry import radio_registry

        if radio_registry.has(radio_id):
            return radio_registry.get(radio_id).ingest_allowed()
    except Exception:
        pass
    return ingest_allowed()


def track_pending_ack(
    expected_ack: str,
    message_id: int,
    timeout_ms: int,
    radio_id: str = "default",
) -> bool:
    """Compatibility wrapper for pending DM ACK tracking."""
    return dm_ack_tracker.track_pending_ack(expected_ack, message_id, timeout_ms, radio_id=radio_id)


def cleanup_expired_acks() -> None:
    """Compatibility wrapper for expiring stale DM ACK entries."""
    dm_ack_tracker.cleanup_expired_acks()


async def on_contact_message(event: "Event", radio_id: str = "default") -> None:
    """Handle incoming direct messages from MeshCore library.

    NOTE: DMs are primarily handled by the packet processor via RX_LOG_DATA,
    which decrypts using our exported private key. This handler exists as a
    fallback for cases where:
    1. The private key couldn't be exported (firmware without ENABLE_PRIVATE_KEY_EXPORT)
    2. The packet processor couldn't match the sender to a known contact

    The packet processor handles: decryption, storage, broadcast, bot trigger.
    This handler adapts CONTACT_MSG_RECV payloads into the shared DM ingest
    workflow, which reconciles duplicates against the packet pipeline when possible.
    """
    if not _is_ingest_allowed(radio_id):
        logger.debug("Skipping CONTACT_MSG_RECV because radio ingest is closed")
        return
    payload = event.payload

    # Skip CLI command responses (txt_type=1) - these are handled by the command endpoint
    txt_type = payload.get("txt_type", 0)
    if txt_type == 1:
        logger.debug("Skipping CLI response from %s (txt_type=1)", payload.get("pubkey_prefix"))
        return

    # Get full public key if available, otherwise use prefix
    sender_pubkey = payload.get("public_key") or payload.get("pubkey_prefix", "")
    received_at = int(time.time())

    context = await resolve_fallback_direct_message_context(
        sender_public_key=sender_pubkey,
        received_at=received_at,
        broadcast_fn=broadcast_event,
        contact_repository=ContactRepository,
        log=logger,
        radio_id=radio_id,
    )
    if context.skip_storage:
        logger.debug(
            "Skipping message from repeater %s (not stored in chat history)",
            context.conversation_key[:12],
        )
        return

    # Try to create or reconcile the message via the shared DM ingest service.
    ts = payload.get("sender_timestamp")
    sender_timestamp = ts if ts is not None else received_at
    path = payload.get("path")
    path_len = payload.get("path_len")
    sender_name = context.sender_name
    sender_key = context.sender_key
    signature = payload.get("signature")
    if (
        context.contact is not None
        and context.contact.type == CONTACT_TYPE_ROOM
        and txt_type == 2
        and isinstance(signature, str)
        and signature
    ):
        sender_name, sender_key = await resolve_direct_message_sender_metadata(
            sender_public_key=signature,
            received_at=received_at,
            broadcast_fn=broadcast_event,
            contact_repository=ContactRepository,
            log=logger,
            radio_id=radio_id,
        )
    message = await ingest_fallback_direct_message(
        conversation_key=context.conversation_key,
        text=payload.get("text", ""),
        sender_timestamp=sender_timestamp,
        received_at=received_at,
        path=path,
        path_len=path_len,
        txt_type=txt_type,
        signature=signature,
        sender_name=sender_name,
        sender_key=sender_key,
        broadcast_fn=broadcast_event,
        update_last_contacted_key=context.contact.public_key.lower() if context.contact else None,
        radio_id=radio_id,
    )

    if message is None:
        # Already handled by packet processor (or exact duplicate) - nothing more to do
        logger.debug(
            "DM from %s already processed by packet processor", context.conversation_key[:12]
        )
        return

    # If we get here, the packet processor didn't handle this message
    # (likely because private key export is not available)
    logger.debug(
        "DM from %s handled by event handler (fallback path)", context.conversation_key[:12]
    )


async def on_rx_log_data(event: "Event", radio_id: str = "default") -> None:
    """Store raw RF packet data and process via centralized packet processor.

    This is the unified entry point for all RF packets. The packet processor
    handles channel messages (GROUP_TEXT) and advertisements (ADVERT).
    """
    if not _is_ingest_allowed(radio_id):
        logger.debug("Skipping RX_LOG_DATA because radio ingest is closed")
        return
    payload = event.payload
    logger.debug("Received RX log data packet")

    if "payload" not in payload:
        logger.warning("RX_LOG_DATA event missing 'payload' field")
        return

    raw_hex = payload["payload"]
    raw_bytes = bytes.fromhex(raw_hex)

    if radio_id == "default":
        await process_raw_packet(
            raw_bytes=raw_bytes,
            snr=payload.get("snr"),
            rssi=payload.get("rssi"),
        )
    else:
        await process_raw_packet(
            raw_bytes=raw_bytes,
            snr=payload.get("snr"),
            rssi=payload.get("rssi"),
            radio_id=radio_id,
        )


async def on_path_update(event: "Event", radio_id: str = "default") -> None:
    """Handle path update events."""
    if not _is_ingest_allowed(radio_id):
        logger.debug("Skipping PATH_UPDATE because radio ingest is closed")
        return
    payload = event.payload
    public_key = str(payload.get("public_key", "")).lower()
    pubkey_prefix = str(payload.get("pubkey_prefix", "")).lower()

    contact: Contact | None = None
    if public_key:
        logger.debug("Path update for %s", public_key[:12])
        contact = await ContactRepository.get_by_key(public_key, radio_id=radio_id)
    elif pubkey_prefix:
        # Legacy compatibility: older payloads may only include a prefix.
        logger.debug("Path update for prefix %s", pubkey_prefix)
        contact = await ContactRepository.get_by_key_prefix(pubkey_prefix, radio_id=radio_id)
    else:
        logger.debug("PATH_UPDATE missing public_key/pubkey_prefix, skipping")
        return

    if not contact:
        return

    # PATH_UPDATE is a serial control push event from firmware (not an RF packet).
    # Current meshcore payloads only include public_key for this event.
    # RF route/path bytes are handled via RX_LOG_DATA -> process_raw_packet,
    # so if path fields are absent here we treat this as informational only.
    path = payload.get("path")
    path_len = payload.get("path_len")
    path_hash_mode = payload.get("path_hash_mode")
    if path is None or path_len is None:
        logger.debug(
            "PATH_UPDATE for %s has no path payload, skipping DB update", contact.public_key[:12]
        )
        return

    try:
        normalized_path_len = int(path_len)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid path_len in PATH_UPDATE for %s: %r", contact.public_key[:12], path_len
        )
        return

    normalized_path_hash_mode: int | None
    if path_hash_mode is None:
        # Legacy firmware/library payloads only support 1-byte hop hashes.
        normalized_path_hash_mode = -1 if normalized_path_len == -1 else 0
    else:
        try:
            normalized_path_hash_mode = int(path_hash_mode)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid path_hash_mode in PATH_UPDATE for %s: %r",
                contact.public_key[:12],
                path_hash_mode,
            )
            normalized_path_hash_mode = None

    await ContactRepository.update_direct_path(
        contact.public_key,
        str(path),
        normalized_path_len,
        normalized_path_hash_mode,
        updated_at=int(time.time()),
        radio_id=radio_id,
    )


async def on_new_contact(event: "Event", radio_id: str = "default") -> None:
    """Handle new contact from radio's internal contact database.

    This is different from RF advertisements - these are contacts synced
    from the radio's stored contact list.
    """
    if not _is_ingest_allowed(radio_id):
        logger.debug("Skipping NEW_CONTACT because radio ingest is closed")
        return
    payload = event.payload
    public_key = payload.get("public_key", "")

    if not public_key:
        logger.warning("Received new contact event with no public_key, skipping")
        return

    logger.debug("New contact: %s", public_key[:12])

    contact_upsert = ContactUpsert.from_radio_dict(
        public_key.lower(), payload, on_radio=False, radio_id=radio_id
    )

    # Block new contacts whose type is in discovery_blocked_types, matching
    # the same guard in _process_advertisement.  Existing contacts (already
    # in the DB) are always updated.
    existing = await ContactRepository.get_by_key(public_key.lower(), radio_id=radio_id)
    contact_type = contact_upsert.type or 0
    if existing is None and contact_type > 0:
        from app.repository import AppSettingsRepository

        settings = await AppSettingsRepository.get()
        if contact_type in settings.discovery_blocked_types:
            logger.debug(
                "Skipping new contact %s: type %d is in discovery_blocked_types",
                public_key[:12],
                contact_type,
            )
            return

    # Intentionally do not set first_seen or last_seen here: NEW_CONTACT
    # fires from the radio's stored contact DB, not an RF observation.
    # Both first_seen and last_seen are RF-only timestamps — they track
    # the first and most recent time we actually heard this pubkey over
    # the air (adverts, messages, path updates). Contacts synced from the
    # radio's internal DB without any RF activity stay NULL until a real
    # RF observation fills them in.
    inserted = await ContactRepository.upsert_reporting_insert(contact_upsert, radio_id=radio_id)
    promoted_keys = await promote_prefix_contacts_for_contact(
        public_key=public_key,
        log=logger,
        radio_id=radio_id,
    )

    adv_name = payload.get("adv_name")
    await record_contact_name_and_reconcile(
        public_key=public_key,
        contact_name=adv_name,
        timestamp=int(time.time()),
        log=logger,
        radio_id=radio_id,
    )

    # Read back from DB so the broadcast includes all fields (last_contacted,
    # last_read_at, etc.) matching the REST Contact shape exactly.
    db_contact = await ContactRepository.get_by_key(public_key, radio_id=radio_id)
    contact_payload = (
        db_contact.model_dump()
        if db_contact
        else Contact(**contact_upsert.model_dump(exclude_none=True)).model_dump()
    )
    if radio_id != "default":
        broadcast_event("contact", contact_payload, radio_id=radio_id)
    else:
        broadcast_event("contact", contact_payload)
    if db_contact:
        for old_key in promoted_keys:
            resolved_payload = {
                "previous_public_key": old_key,
                "contact": db_contact.model_dump(),
            }
            if radio_id != "default":
                broadcast_event("contact_resolved", resolved_payload, radio_id=radio_id)
            else:
                broadcast_event("contact_resolved", resolved_payload)

    if inserted:
        from app.push.first_seen import maybe_notify_contact_first_seen

        notify_contact = db_contact or Contact(**contact_upsert.model_dump(exclude_none=True))
        await maybe_notify_contact_first_seen(
            notify_contact,
            origin="radio_event",
            promoted_keys=promoted_keys,
        )


async def on_ack(event: "Event", radio_id: str = "default") -> None:
    """Handle ACK events for direct messages."""
    if not _is_ingest_allowed(radio_id):
        logger.debug("Skipping ACK because radio ingest is closed")
        return
    payload = event.payload
    ack_code = payload.get("code", "")

    if not ack_code:
        logger.debug("Received ACK with no code")
        return

    logger.debug("Received ACK with code %s (radio_id=%s)", ack_code, radio_id)
    matched = await apply_dm_ack_code(ack_code, broadcast_fn=broadcast_event, radio_id=radio_id)
    if matched:
        logger.info("ACK received for code %s (radio_id=%s)", ack_code, radio_id)
    else:
        logger.debug(
            "ACK code %s does not match any pending messages (radio_id=%s)", ack_code, radio_id
        )


async def on_library_connected(event: "Event") -> None:
    """Re-run the identity gate when meshcore_py auto-reconnects the transport."""
    payload = event.payload if isinstance(getattr(event, "payload", None), dict) else {}
    if not payload.get("reconnected"):
        return
    from app.services.radio_identity import evaluate_connected_identity
    from app.services.radio_ingest_gate import deny_ingest
    from app.services.radio_runtime import radio_runtime
    from app.websocket import broadcast_health

    deny_ingest()
    radio_runtime._setup_complete = False
    mc = radio_runtime.meshcore
    if mc is None:
        return
    decision = await evaluate_connected_identity(mc)
    if decision != "continue":
        logger.warning("Library reconnect blocked by radio identity gate")
        await radio_runtime.pause_connection()
        broadcast_health(False, radio_runtime.connection_info)


async def on_library_disconnected(_event: "Event") -> None:
    """Close ingest as soon as meshcore_py drops the transport."""
    from app.services.radio_ingest_gate import deny_ingest
    from app.services.radio_runtime import radio_runtime

    deny_ingest()
    radio_runtime._setup_complete = False


def unregister_event_handlers(radio_instance: Any = None) -> None:
    """Drop MeshCore subscriptions without registering replacements.

    If radio_instance is provided, unsubscribes its specific subscriptions.
    Otherwise drops default process-wide active subscriptions.
    """
    global _active_subscriptions
    if radio_instance is not None:
        subs = getattr(radio_instance, "_subscriptions", None)
        if subs is not None:
            for sub in list(subs):
                try:
                    sub.unsubscribe()
                except Exception:
                    pass
            subs.clear()
        if getattr(radio_instance, "radio_id", None) == "default":
            for sub in list(_active_subscriptions):
                try:
                    sub.unsubscribe()
                except Exception:
                    pass
            _active_subscriptions.clear()
        return

    for sub in list(_active_subscriptions):
        try:
            sub.unsubscribe()
        except Exception:
            pass
    _active_subscriptions.clear()


def register_event_handlers(meshcore: Any, radio_instance: Any = None) -> None:
    """Register event handlers with the MeshCore instance.

    Note: CHANNEL_MSG_RECV and ADVERTISEMENT events are NOT subscribed.
    These are handled by the packet processor via RX_LOG_DATA to avoid
    duplicate processing and ensure consistent handling.

    This function is safe to call multiple times (e.g., after reconnect).
    Existing handlers are unsubscribed before new ones are registered.
    """
    global _active_subscriptions

    # Unsubscribe existing handlers for this radio (or globally) to prevent duplication
    unregister_event_handlers(radio_instance)

    radio_id = "default"
    if radio_instance is not None:
        radio_id = getattr(radio_instance, "radio_id", "default")

    async def _handle_contact_message(event: "Event") -> None:
        await on_contact_message(event, radio_id=radio_id)

    async def _handle_rx_log_data(event: "Event") -> None:
        await on_rx_log_data(event, radio_id=radio_id)

    async def _handle_path_update(event: "Event") -> None:
        await on_path_update(event, radio_id=radio_id)

    async def _handle_new_contact(event: "Event") -> None:
        await on_new_contact(event, radio_id=radio_id)

    async def _handle_ack(event: "Event") -> None:
        await on_ack(event, radio_id=radio_id)

    new_subs = [
        meshcore.subscribe(EventType.CONTACT_MSG_RECV, _handle_contact_message),
        meshcore.subscribe(EventType.RX_LOG_DATA, _handle_rx_log_data),
        meshcore.subscribe(EventType.PATH_UPDATE, _handle_path_update),
        meshcore.subscribe(EventType.NEW_CONTACT, _handle_new_contact),
        meshcore.subscribe(EventType.ACK, _handle_ack),
    ]
    if hasattr(EventType, "CONNECTED"):
        new_subs.append(meshcore.subscribe(EventType.CONNECTED, on_library_connected))
    if hasattr(EventType, "DISCONNECTED"):
        new_subs.append(meshcore.subscribe(EventType.DISCONNECTED, on_library_disconnected))

    if radio_instance is not None:
        subs = getattr(radio_instance, "_subscriptions", None)
        if subs is not None:
            subs.extend(new_subs)
        if getattr(radio_instance, "radio_id", None) == "default":
            _active_subscriptions.extend(new_subs)
    else:
        _active_subscriptions.extend(new_subs)

    logger.info("Event handlers registered for radio_id=%s", radio_id)
