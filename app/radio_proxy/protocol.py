"""Companion TCP framing and v1 payload codecs for the Meshloom radio proxy."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from meshcore.packets import CommandType, PacketType

RX_MARKER = 0x3C  # client → proxy
TX_MARKER = 0x3E  # proxy → client
MAX_FRAME_SIZE = 300
INSTANCE_ID_HEX_LEN = 12
PROXY_MODEL_PREFIX = "MeshloomProxy/"
DEVICE_INFO_FW_VER = 10
PATH_LEN_FLOOD = 255


def make_instance_id() -> str:
    return uuid.uuid4().hex[:INSTANCE_ID_HEX_LEN]


def proxy_model_string(instance_id: str) -> str:
    token = instance_id.lower()[:INSTANCE_ID_HEX_LEN].ljust(INSTANCE_ID_HEX_LEN, "0")
    return f"{PROXY_MODEL_PREFIX}{token}"


def parse_proxy_instance_id(model: str | None) -> str | None:
    if not model:
        return None
    trimmed = model.strip()
    if not trimmed.startswith(PROXY_MODEL_PREFIX):
        return None
    token = trimmed[len(PROXY_MODEL_PREFIX) :].split("\0", 1)[0].strip().lower()
    if len(token) < INSTANCE_ID_HEX_LEN:
        return None
    return token[:INSTANCE_ID_HEX_LEN]


def encode_tx_frame(payload: bytes) -> bytes:
    return bytes([TX_MARKER]) + len(payload).to_bytes(2, "little") + payload


def _pad(data: bytes, size: int) -> bytes:
    return data[:size].ljust(size, b"\x00")


def _fixed_ascii(value: str, size: int) -> bytes:
    return _pad(value.encode("utf-8", "replace"), size)


def encode_ok(value: int | None = None) -> bytes:
    if value is None:
        return bytes([PacketType.OK.value])
    return bytes([PacketType.OK.value]) + int(value).to_bytes(4, "little")


def encode_error(error_code: int = 1) -> bytes:
    return bytes([PacketType.ERROR.value, error_code & 0xFF])


def encode_disabled() -> bytes:
    return bytes([PacketType.DISABLED.value])


def encode_no_more_msgs() -> bytes:
    return bytes([PacketType.NO_MORE_MSGS.value])


def encode_messages_waiting() -> bytes:
    return bytes([PacketType.MESSAGES_WAITING.value])


def encode_current_time(unix_ts: int) -> bytes:
    return bytes([PacketType.CURRENT_TIME.value]) + int(unix_ts).to_bytes(4, "little")


def encode_battery(*, level_mv: int = 0, used_kb: int = 0, total_kb: int = 0) -> bytes:
    return (
        bytes([PacketType.BATTERY.value])
        + int(level_mv).to_bytes(2, "little")
        + int(used_kb).to_bytes(4, "little")
        + int(total_kb).to_bytes(4, "little")
    )


def encode_self_info(
    *,
    public_key: bytes,
    name: str,
    adv_type: int = 1,
    tx_power: int = 0,
    max_tx_power: int = 22,
    lat: float = 0.0,
    lon: float = 0.0,
    multi_acks: int = 0,
    adv_loc_policy: int = 0,
    telemetry_mode: int = 0,
    manual_add_contacts: bool = False,
    radio_freq: float = 0.0,
    radio_bw: float = 0.0,
    radio_sf: int = 0,
    radio_cr: int = 0,
) -> bytes:
    if len(public_key) != 32:
        raise ValueError("public_key must be 32 bytes")
    payload = bytearray(
        [PacketType.SELF_INFO.value, adv_type & 0xFF, tx_power & 0xFF, max_tx_power & 0xFF]
    )
    payload.extend(public_key)
    payload.extend(int(lat * 1e6).to_bytes(4, "little", signed=True))
    payload.extend(int(lon * 1e6).to_bytes(4, "little", signed=True))
    payload.append(multi_acks & 0xFF)
    payload.append(adv_loc_policy & 0xFF)
    payload.append(telemetry_mode & 0xFF)
    payload.append(1 if manual_add_contacts else 0)
    payload.extend(int(radio_freq * 1000).to_bytes(4, "little"))
    payload.extend(int(radio_bw * 1000).to_bytes(4, "little"))
    payload.append(radio_sf & 0xFF)
    payload.append(radio_cr & 0xFF)
    payload.extend(name.encode("utf-8", "replace"))
    return bytes(payload)


def encode_device_info(
    *,
    instance_id: str,
    max_contacts: int = 510,
    max_channels: int = 40,
    fw_build: str = "",
    version: str = "",
    path_hash_mode: int = 0,
    ble_pin: int = 0,
) -> bytes:
    contacts_byte = min(255, max(1, max_contacts) // 2)
    channels_byte = min(255, max(1, max_channels))
    model = proxy_model_string(instance_id)
    payload = bytearray([PacketType.DEVICE_INFO.value, DEVICE_INFO_FW_VER])
    payload.append(contacts_byte)
    payload.append(channels_byte)
    payload.extend(int(ble_pin).to_bytes(4, "little"))
    payload.extend(_fixed_ascii(fw_build, 12))
    payload.extend(_fixed_ascii(model, 40))
    payload.extend(_fixed_ascii(version, 20))
    payload.append(0)  # repeat
    payload.append(path_hash_mode & 0xFF)
    return bytes(payload)


def encode_contact_start(count: int) -> bytes:
    return bytes([PacketType.CONTACT_START.value]) + int(count).to_bytes(4, "little")


def encode_contact(
    *,
    public_key: bytes,
    contact_type: int = 1,
    flags: int = 0,
    out_path: bytes = b"",
    out_path_len: int = -1,
    out_path_hash_mode: int = 0,
    name: str = "",
    last_advert: int = 0,
    lat: float = 0.0,
    lon: float = 0.0,
    lastmod: int = 0,
) -> bytes:
    if len(public_key) != 32:
        raise ValueError("public_key must be 32 bytes")
    if out_path_len < 0:
        path_byte = PATH_LEN_FLOOD
        path = b"\x00" * 64
    else:
        path_byte = (out_path_len & 0x3F) | ((out_path_hash_mode & 0x03) << 6)
        path = _pad(out_path, 64)
    payload = bytearray([PacketType.CONTACT.value])
    payload.extend(public_key)
    payload.append(contact_type & 0xFF)
    payload.append(flags & 0xFF)
    payload.append(path_byte)
    payload.extend(path)
    payload.extend(_fixed_ascii(name, 32))
    payload.extend(int(last_advert).to_bytes(4, "little"))
    payload.extend(int(lat * 1e6).to_bytes(4, "little", signed=True))
    payload.extend(int(lon * 1e6).to_bytes(4, "little", signed=True))
    payload.extend(int(lastmod).to_bytes(4, "little"))
    return bytes(payload)


def encode_contact_end(most_recent_lastmod: int = 0) -> bytes:
    return bytes([PacketType.CONTACT_END.value]) + int(most_recent_lastmod).to_bytes(4, "little")


def encode_channel_info(*, channel_idx: int, name: str = "", secret: bytes = b"\x00" * 16) -> bytes:
    return (
        bytes([PacketType.CHANNEL_INFO.value, channel_idx & 0xFF])
        + _fixed_ascii(name, 32)
        + _pad(secret, 16)
    )


def encode_msg_sent(*, msg_type: int, expected_ack: bytes, suggested_timeout_ms: int) -> bytes:
    ack = _pad(expected_ack, 4)
    return (
        bytes([PacketType.MSG_SENT.value, msg_type & 0xFF])
        + ack
        + int(suggested_timeout_ms).to_bytes(4, "little")
    )


def encode_contact_msg_recv(
    *,
    pubkey_prefix: bytes,
    text: str,
    sender_timestamp: int,
    txt_type: int = 0,
    path_len: int = PATH_LEN_FLOOD,
) -> bytes:
    payload = bytearray([PacketType.CONTACT_MSG_RECV.value])
    payload.extend(_pad(pubkey_prefix, 6))
    payload.append(path_len & 0xFF)
    payload.append(txt_type & 0xFF)
    payload.extend(int(sender_timestamp).to_bytes(4, "little"))
    payload.extend(text.encode("utf-8", "replace"))
    return bytes(payload)


def encode_channel_msg_recv(
    *,
    channel_idx: int,
    text: str,
    sender_timestamp: int,
    txt_type: int = 0,
    path_len: int = PATH_LEN_FLOOD,
) -> bytes:
    payload = bytearray([PacketType.CHANNEL_MSG_RECV.value])
    payload.append(channel_idx & 0xFF)
    payload.append(path_len & 0xFF)
    payload.append(txt_type & 0xFF)
    payload.extend(int(sender_timestamp).to_bytes(4, "little"))
    payload.extend(text.encode("utf-8", "replace"))
    return bytes(payload)


def encode_ack(*, ack_code: bytes, trip_time_ms: int = 0) -> bytes:
    return (
        bytes([PacketType.ACK.value]) + _pad(ack_code, 4) + int(trip_time_ms).to_bytes(4, "little")
    )


def encode_log_data(*, payload: bytes, snr: float = 0.0, rssi: int = 0) -> bytes:
    snr_byte = max(-128, min(127, int(round(snr * 4)))) & 0xFF
    rssi_byte = max(-128, min(127, int(rssi))) & 0xFF
    return bytes([PacketType.LOG_DATA.value, snr_byte, rssi_byte]) + payload


@dataclass(frozen=True)
class DecodedCommand:
    code: int
    raw: bytes


@dataclass(frozen=True)
class SendTxtCommand:
    txt_type: int
    attempt: int
    timestamp: int
    dest: bytes
    text: str


@dataclass(frozen=True)
class SendChanCommand:
    txt_type: int
    channel_idx: int
    timestamp: int
    text: str


@dataclass(frozen=True)
class SetChannelCommand:
    channel_idx: int
    name: str
    secret: bytes


@dataclass(frozen=True)
class AddContactCommand:
    public_key: bytes
    contact_type: int
    name: str


class FrameAssembler:
    """Accumulate client→proxy 0x3C frames from a TCP stream."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf.extend(data)
        frames: list[bytes] = []
        while True:
            start = self._buf.find(bytes([RX_MARKER]))
            if start < 0:
                self._buf.clear()
                return frames
            if start > 0:
                del self._buf[:start]
            if len(self._buf) < 3:
                return frames
            size = int.from_bytes(self._buf[1:3], "little")
            if size > MAX_FRAME_SIZE:
                del self._buf[0]
                continue
            total = 3 + size
            if len(self._buf) < total:
                return frames
            frames.append(bytes(self._buf[3:total]))
            del self._buf[:total]


def decode_command(payload: bytes) -> DecodedCommand:
    if not payload:
        raise ValueError("empty companion command")
    return DecodedCommand(code=payload[0], raw=payload)


def parse_send_txt(payload: bytes) -> SendTxtCommand:
    if len(payload) < 9:
        raise ValueError("SEND_TXT_MSG too short")
    txt_type = payload[1]
    attempt = payload[2]
    timestamp = int.from_bytes(payload[3:7], "little")
    rest = payload[7:]
    if len(rest) >= 32:
        dest, text_bytes = rest[:32], rest[32:]
    elif len(rest) >= 6:
        dest, text_bytes = rest[:6], rest[6:]
    else:
        raise ValueError("SEND_TXT_MSG missing destination")
    return SendTxtCommand(
        txt_type=txt_type,
        attempt=attempt,
        timestamp=timestamp,
        dest=dest,
        text=text_bytes.decode("utf-8", "replace"),
    )


def parse_send_chan(payload: bytes) -> SendChanCommand:
    if len(payload) < 8:
        raise ValueError("SEND_CHANNEL_TXT_MSG too short")
    return SendChanCommand(
        txt_type=payload[1],
        channel_idx=payload[2],
        timestamp=int.from_bytes(payload[3:7], "little"),
        text=payload[7:].decode("utf-8", "replace"),
    )


def parse_set_channel(payload: bytes) -> SetChannelCommand:
    if len(payload) < 50:
        raise ValueError("SET_CHANNEL too short")
    name = payload[2:34].split(b"\x00", 1)[0].decode("utf-8", "replace")
    return SetChannelCommand(channel_idx=payload[1], name=name, secret=payload[34:50])


def parse_add_contact(payload: bytes) -> AddContactCommand:
    if len(payload) < 35:
        raise ValueError("ADD_CONTACT too short")
    name = ""
    if len(payload) >= 131:
        name = payload[99:131].split(b"\x00", 1)[0].decode("utf-8", "replace")
    return AddContactCommand(
        public_key=payload[1:33],
        contact_type=payload[33],
        name=name,
    )


def parse_get_channel_idx(payload: bytes) -> int:
    if len(payload) < 2:
        raise ValueError("GET_CHANNEL missing index")
    return payload[1]


def parse_remove_contact_key(payload: bytes) -> bytes:
    if len(payload) < 33:
        raise ValueError("REMOVE_CONTACT missing key")
    return payload[1:33]


def parse_get_contacts_since(payload: bytes) -> int:
    if len(payload) >= 5:
        return int.from_bytes(payload[1:5], "little")
    return 0


def is_empty_channel_secret(secret: bytes) -> bool:
    return not secret or all(b == 0 for b in secret)


def command_name(code: int) -> str:
    try:
        return CommandType(code).name
    except ValueError:
        return f"CMD_{code:#x}"
