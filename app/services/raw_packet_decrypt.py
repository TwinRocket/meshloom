"""Attach decrypted raw-packet info for WS and GET /packets/{id}."""

import hashlib

from app.decoder import PayloadType, parse_packet, try_decrypt_group_data
from app.models import RawPacketDecryptedInfo, RawPacketGroupData
from app.repository import ChannelRepository, MessageRepository


def _group_data_text(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


async def _info_from_message(message_id: int) -> RawPacketDecryptedInfo | None:
    message = await MessageRepository.get_by_id(message_id)
    if message is None:
        return None
    if message.type == "CHAN":
        channel = await ChannelRepository.get_by_key(message.conversation_key)
        return RawPacketDecryptedInfo(
            channel_name=channel.name if channel else None,
            sender=message.sender_name,
            channel_key=message.conversation_key,
            contact_key=message.sender_key,
            sender_timestamp=message.sender_timestamp,
            message=message.text,
            group_data=None,
        )
    return RawPacketDecryptedInfo(
        sender=message.sender_name,
        contact_key=message.conversation_key,
        sender_timestamp=message.sender_timestamp,
        message=message.text,
        group_data=None,
    )


async def _info_from_group_data(raw: bytes) -> RawPacketDecryptedInfo | None:
    packet_info = parse_packet(raw)
    if packet_info is None or packet_info.payload_type != PayloadType.GROUP_DATA:
        return None
    if packet_info.payload_version != 0:
        return None
    if len(packet_info.payload) < 1:
        return None

    hash_byte = packet_info.payload[0]
    channels = await ChannelRepository.get_all()
    for channel in channels:
        try:
            channel_key_bytes = bytes.fromhex(channel.key)
        except ValueError:
            continue
        if hashlib.sha256(channel_key_bytes).digest()[0] != hash_byte:
            continue
        parsed = try_decrypt_group_data(raw, channel_key_bytes)
        if parsed is None:
            continue
        return RawPacketDecryptedInfo(
            channel_name=channel.name,
            sender=None,
            channel_key=channel.key,
            contact_key=None,
            sender_timestamp=None,
            message=None,
            group_data=RawPacketGroupData(
                data_type=parsed.data_type,
                data_len=parsed.data_len,
                data_hex=parsed.data.hex(),
                data_text=_group_data_text(parsed.data),
            ),
        )
    return None


async def attach_raw_packet_decrypted_info(
    raw: bytes, message_id: int | None
) -> tuple[bool, RawPacketDecryptedInfo | None]:
    """Build decrypted_info for a stored or live raw packet.

    1. Linked CHAN/PRIV message: same fields as GET /{id} historically, group_data=None.
    2. Else GROUP_DATA v0: try every local channel key whose SHA256[0] matches; first
       MAC OK wins. No messages row and no mark_decrypted.
    3. Else no decrypted info. GROUP_TEXT is never parsed as GroupData here.
    """
    if message_id is not None:
        info = await _info_from_message(message_id)
        if info is not None:
            return True, info
    info = await _info_from_group_data(raw)
    if info is not None:
        return True, info
    return False, None
