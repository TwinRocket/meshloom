import logging
import time
from hashlib import sha256
from sqlite3 import OperationalError

import aiosqlite
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.database import db
from app.decoder import PayloadType, parse_packet, try_decrypt_packet_with_channel_key
from app.models import (
    RawPacketDetail,
    RawPacketHistoryResponse,
    UndecryptedGroupTextSample,
    UndecryptedGroupTextSamplesResponse,
)
from app.packet_processor import create_message_from_decrypted, run_historical_dm_decryption
from app.region_resolver import resolve_region
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    RawPacketRepository,
)
from app.services.messages import backfill_message_regions
from app.services.raw_packet_decrypt import attach_raw_packet_decrypted_info
from app.services.test_channel import drop_test_channel_samples
from app.websocket import broadcast_success

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/packets", tags=["packets"])


class DecryptRequest(BaseModel):
    key_type: str = Field(description="Type of key: 'channel' or 'contact'")
    channel_key: str | None = Field(
        default=None, description="Channel key as hex (16 bytes = 32 chars)"
    )
    channel_name: str | None = Field(
        default=None, description="Channel name (for hashtag channels, key derived from name)"
    )
    # Fields for contact (DM) decryption
    private_key: str | None = Field(
        default=None,
        description="Our private key as hex (64 bytes = 128 chars, Ed25519 seed + pubkey)",
    )
    contact_public_key: str | None = Field(
        default=None, description="Contact's public key as hex (32 bytes = 64 chars)"
    )


class DecryptResult(BaseModel):
    started: bool
    total_packets: int
    message: str


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


async def _run_historical_channel_decryption(
    channel_key_bytes: bytes, channel_key_hex: str, display_name: str | None = None
) -> None:
    """Background task to decrypt historical packets with a channel key."""
    total = await RawPacketRepository.get_undecrypted_count()
    decrypted_count = 0

    if total == 0:
        logger.info("No undecrypted packets to process")
        return

    logger.info("Starting historical channel decryption of %d packets", total)

    known_regions = (await AppSettingsRepository.get()).known_regions

    async for (
        packet_id,
        packet_data,
        packet_timestamp,
    ) in RawPacketRepository.stream_all_undecrypted():
        result = try_decrypt_packet_with_channel_key(packet_data, channel_key_bytes)

        if result is not None:
            # Extract path from the raw packet for storage
            packet_info = parse_packet(packet_data)
            path_hex = packet_info.path.hex() if packet_info else None

            # Resolve regional flood-scope if this is a transport-routed packet.
            transport_code: int | None = None
            region: str | None = None
            if packet_info is not None and packet_info.transport_codes is not None:
                transport_code = packet_info.transport_codes[0]
                region = resolve_region(
                    int(packet_info.payload_type),
                    packet_info.payload,
                    transport_code,
                    known_regions,
                )

            msg_id = await create_message_from_decrypted(
                packet_id=packet_id,
                channel_key=channel_key_hex,
                channel_name=display_name,
                sender=result.sender,
                message_text=result.message,
                timestamp=result.timestamp,
                received_at=packet_timestamp,
                path=path_hex,
                path_len=packet_info.path_length if packet_info else None,
                realtime=False,  # Historical decryption should not trigger fanout
                transport_code=transport_code,
                region=region,
            )

            if msg_id is not None:
                decrypted_count += 1

    logger.info(
        "Historical channel decryption complete: %d/%d packets decrypted", decrypted_count, total
    )

    # Notify frontend
    if decrypted_count > 0:
        name = display_name or channel_key_hex[:12]
        broadcast_success(
            f"Historical decrypt complete for {name}",
            f"Decrypted {decrypted_count} message{'s' if decrypted_count != 1 else ''}",
            code="historical_decrypt_complete",
            params={"name": name},
        )


@router.get("/undecrypted/count")
async def get_undecrypted_count() -> dict:
    """Get the count of undecrypted packets."""
    count = await RawPacketRepository.get_undecrypted_count()
    return {"count": count}


@router.get(
    "/undecrypted/group-text-samples",
    response_model=UndecryptedGroupTextSamplesResponse,
)
async def get_undecrypted_group_text_samples(
    max_hashes: int = 80,
    max_per_hash: int = 4,
    max_scan: int = 8000,
    received_since_days: int = 30,
) -> UndecryptedGroupTextSamplesResponse:
    """Return a bounded newest-first sample of recent undecrypted GROUP_TEXT packets."""
    max_hashes = min(max(max_hashes, 1), 200)
    max_per_hash = min(max(max_per_hash, 1), 16)
    max_scan = min(max(max_scan, 1), 50000)
    received_since_days = min(max(received_since_days, 1), 3650)
    received_since = int(time.time()) - (received_since_days * 86400)

    scanned, packet_count, rows = await RawPacketRepository.get_undecrypted_group_text_samples(
        max_hashes=max_hashes,
        max_per_hash=max_per_hash,
        max_scan=max_scan,
        received_since=received_since,
    )
    samples = [
        UndecryptedGroupTextSample(
            channel_hash=channel_hash,
            packet_id=packet_id,
            data=data.hex(),
            timestamp=timestamp,
            cipher_mac=cipher_mac,
        )
        # Our own test packets are undecryptable by design; the cracker would
        # burn GPU time on a key the app already knows.
        for channel_hash, packet_id, data, timestamp, cipher_mac in drop_test_channel_samples(rows)
    ]
    return UndecryptedGroupTextSamplesResponse(
        hash_count=len({sample.channel_hash for sample in samples}),
        packet_count=packet_count,
        scanned=scanned,
        samples=samples,
    )


@router.post("/region-backfill")
async def backfill_regions() -> dict:
    """Re-resolve region scope for stored channel messages that still have a raw packet.

    Region tagging normally happens at ingest, so messages stored before the feature
    (or before a region name was added to ``known_regions``) have no region. This
    recomputes them. Messages whose raw packet was already purged cannot be
    re-evaluated. Clients should refetch the conversation to see updated badges.
    """
    known_regions = (await AppSettingsRepository.get()).known_regions
    return await backfill_message_regions(known_regions)


HISTORY_ITEM_LIMIT = 5000
HISTORY_SCAN_LIMIT = 20_000


def _payload_type_filter(value: str | None) -> PayloadType | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    compact = stripped.upper().replace("-", "_").replace(" ", "_")
    try:
        return PayloadType[compact]
    except KeyError:
        compact_nosep = compact.replace("_", "")
        for member in PayloadType:
            if member.name.replace("_", "") == compact_nosep:
                return member
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid payload_type: {value}",
        ) from None


async def _history_item_detail(
    packet_id: int,
    packet_data: bytes,
    packet_timestamp: int,
    message_id: int | None,
    known_regions: list[str],
) -> RawPacketDetail:
    packet_info = parse_packet(packet_data)
    payload_type_name = packet_info.payload_type.name if packet_info else "Unknown"
    transport_code: int | None = None
    region: str | None = None
    if packet_info is not None and packet_info.transport_codes is not None:
        transport_code = packet_info.transport_codes[0]
        region = resolve_region(
            int(packet_info.payload_type),
            packet_info.payload,
            transport_code,
            known_regions,
        )
    _, decrypted_info = await attach_raw_packet_decrypted_info(packet_data, message_id)
    return RawPacketDetail(
        id=packet_id,
        timestamp=packet_timestamp,
        data=packet_data.hex(),
        payload_type=payload_type_name,
        decrypted=decrypted_info is not None,
        decrypted_info=decrypted_info,
        transport_code=transport_code,
        region=region,
    )


@router.get("/history", response_model=RawPacketHistoryResponse)
async def get_raw_packet_history(
    payload_type: str | None = Query(default=None),
    since: int | None = Query(default=None),
    until: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=HISTORY_ITEM_LIMIT),
    after_id: int | None = Query(default=None, ge=1),
    max_scan: int = Query(default=HISTORY_SCAN_LIMIT, ge=1, le=HISTORY_SCAN_LIMIT),
) -> RawPacketHistoryResponse:
    """Return a bounded newest-first page of stored raw packets."""
    if since is not None and until is not None and since > until:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="since must be less than or equal to until",
        )

    type_filter = _payload_type_filter(payload_type)
    rows, total, truncated, scanned = await RawPacketRepository.list_history(
        since=since,
        until=until,
        after_id=after_id,
        limit=limit,
        max_scan=max_scan,
        payload_type=type_filter,
    )
    known_regions = (await AppSettingsRepository.get()).known_regions
    items = [
        await _history_item_detail(
            packet_id, packet_data, packet_timestamp, message_id, known_regions
        )
        for packet_id, packet_data, packet_timestamp, message_id in rows
    ]
    return RawPacketHistoryResponse(
        items=items,
        total=total,
        truncated=truncated,
        scanned=scanned,
    )


@router.get("/{packet_id}", response_model=RawPacketDetail)
async def get_raw_packet(packet_id: int) -> RawPacketDetail:
    """Fetch one stored raw packet by row ID for on-demand inspection."""
    packet_row = await RawPacketRepository.get_by_id(packet_id)
    if packet_row is None:
        raise HTTPException(status_code=404, detail="Raw packet not found")

    stored_packet_id, packet_data, packet_timestamp, message_id = packet_row
    packet_info = parse_packet(packet_data)
    payload_type_name = packet_info.payload_type.name if packet_info else "Unknown"

    # Resolve regional flood-scope for transport-routed packets against the
    # current known-region list (we have the raw payload here, so this stays
    # accurate even if the stored message predates a region-list change).
    transport_code: int | None = None
    region: str | None = None
    if packet_info is not None and packet_info.transport_codes is not None:
        transport_code = packet_info.transport_codes[0]
        settings = await AppSettingsRepository.get()
        region = resolve_region(
            int(packet_info.payload_type),
            packet_info.payload,
            transport_code,
            settings.known_regions,
        )

    _, decrypted_info = await attach_raw_packet_decrypted_info(packet_data, message_id)

    return RawPacketDetail(
        id=stored_packet_id,
        timestamp=packet_timestamp,
        data=packet_data.hex(),
        payload_type=payload_type_name,
        decrypted=decrypted_info is not None,
        decrypted_info=decrypted_info,
        transport_code=transport_code,
        region=region,
    )


@router.post("/decrypt/historical", response_model=DecryptResult)
async def decrypt_historical_packets(
    request: DecryptRequest, background_tasks: BackgroundTasks, response: Response
) -> DecryptResult:
    """
    Attempt to decrypt historical packets with the provided key.
    Runs in the background. Multiple decrypt jobs can run concurrently.
    """
    if request.key_type == "channel":
        # Channel decryption
        if request.channel_key:
            try:
                channel_key_bytes = bytes.fromhex(request.channel_key)
                if len(channel_key_bytes) != 16:
                    raise _bad_request("Channel key must be 16 bytes (32 hex chars)")
                channel_key_hex = request.channel_key.upper()
            except ValueError:
                raise _bad_request("Invalid hex string for channel key") from None
        elif request.channel_name:
            channel_key_bytes = sha256(request.channel_name.encode("utf-8")).digest()[:16]
            channel_key_hex = channel_key_bytes.hex().upper()
        else:
            raise _bad_request("Must provide channel_key or channel_name")

        # Get count and lookup channel name for display
        count = await RawPacketRepository.get_undecrypted_count()
        if count == 0:
            return DecryptResult(
                started=False, total_packets=0, message="No undecrypted packets to process"
            )

        # Try to find channel name for display
        channel = await ChannelRepository.get_by_key(channel_key_hex)
        display_name = channel.name if channel else request.channel_name

        background_tasks.add_task(
            _run_historical_channel_decryption, channel_key_bytes, channel_key_hex, display_name
        )
        response.status_code = status.HTTP_202_ACCEPTED

        return DecryptResult(
            started=True,
            total_packets=count,
            message=f"Started channel decryption of {count} packets in background",
        )

    elif request.key_type == "contact":
        # DM decryption
        if not request.private_key:
            raise _bad_request("Must provide private_key for contact decryption")
        if not request.contact_public_key:
            raise _bad_request("Must provide contact_public_key for contact decryption")

        try:
            private_key_bytes = bytes.fromhex(request.private_key)
            if len(private_key_bytes) != 64:
                raise _bad_request("Private key must be 64 bytes (128 hex chars)")
        except ValueError:
            raise _bad_request("Invalid hex string for private key") from None

        try:
            contact_public_key_bytes = bytes.fromhex(request.contact_public_key)
            if len(contact_public_key_bytes) != 32:
                raise _bad_request("Contact public key must be 32 bytes (64 hex chars)")
            contact_public_key_hex = request.contact_public_key.lower()
        except ValueError:
            raise _bad_request("Invalid hex string for contact public key") from None

        count = await RawPacketRepository.count_undecrypted_text_messages()
        if count == 0:
            return DecryptResult(
                started=False,
                total_packets=0,
                message="No undecrypted TEXT_MESSAGE packets to process",
            )

        # Try to find contact name for display
        from app.repository import ContactRepository

        contact = await ContactRepository.get_by_key(contact_public_key_hex)
        display_name = contact.name if contact else None

        background_tasks.add_task(
            run_historical_dm_decryption,
            private_key_bytes,
            contact_public_key_bytes,
            contact_public_key_hex,
            display_name,
        )
        response.status_code = status.HTTP_202_ACCEPTED

        return DecryptResult(
            started=True,
            total_packets=count,
            message=f"Started DM decryption of {count} TEXT_MESSAGE packets in background",
        )

    raise _bad_request("key_type must be 'channel' or 'contact'")


class MaintenanceRequest(BaseModel):
    prune_undecrypted_days: int | None = Field(
        default=None, ge=1, description="Delete undecrypted packets older than this many days"
    )
    purge_linked_raw_packets: bool = Field(
        default=False,
        description="Delete raw packets already linked to a stored message",
    )


class MaintenanceResult(BaseModel):
    packets_deleted: int
    vacuumed: bool


@router.post("/maintenance", response_model=MaintenanceResult)
async def run_maintenance(request: MaintenanceRequest) -> MaintenanceResult:
    """
    Run packet maintenance tasks and reclaim disk space.

    - Optionally deletes undecrypted packets older than the specified number of days
    - Optionally deletes raw packets already linked to stored messages
    - Runs VACUUM to reclaim disk space
    """
    deleted = 0

    if request.prune_undecrypted_days is not None:
        logger.info(
            "Running maintenance: pruning undecrypted packets older than %d days",
            request.prune_undecrypted_days,
        )
        pruned_undecrypted = await RawPacketRepository.prune_old_undecrypted(
            request.prune_undecrypted_days
        )
        deleted += pruned_undecrypted
        logger.info("Deleted %d old undecrypted packets", pruned_undecrypted)

    if request.purge_linked_raw_packets:
        logger.info("Running maintenance: purging raw packets linked to stored messages")
        purged_linked = await RawPacketRepository.purge_linked_to_messages()
        deleted += purged_linked
        logger.info("Deleted %d linked raw packets", purged_linked)

    # Run VACUUM to reclaim space on a dedicated connection.
    # VACUUM requires exclusive access — if the main connection is actively
    # writing (background sync, message processing, etc.) it fails with
    # SQLITE_BUSY. This is expected; we just report vacuumed=False.
    vacuumed = False
    try:
        async with aiosqlite.connect(db.db_path) as vacuum_conn:
            await vacuum_conn.executescript("VACUUM;")
        vacuumed = True
        logger.info("Database vacuumed")
    except OperationalError as e:
        logger.warning("VACUUM skipped (database busy): %s", e)
    except Exception as e:
        logger.error("VACUUM failed unexpectedly: %s", e)

    return MaintenanceResult(packets_deleted=deleted, vacuumed=vacuumed)
