"""Meshloom Remote relay frame codec, protocol v1 (meshloom-community
``docs/contracts/remote-relay.md``, "Frames"; vectors copied in
``tests/fixtures/remote_frames_vectors.json``).

Pure functions, stdlib only. A WebSocket binary message carries one or more frames,
back to back, big-endian: ``type:u8 | stream_id:u32 | flags:u8 | length:u32 |
payload[length]``. ``decode_message`` follows the contract's decoding order:

1. fewer than 10 bytes left (or an empty message): ``frame_truncated``;
2. type 0: ``frame_type_invalid``;
3. fewer bytes left than ``length``: ``frame_truncated``;
4. ``length`` above the type's limit: on stream 0 or an even stream the connection
   closes with ``frame_too_large``; on an odd stream it is a stream error (the payload
   is skipped, the stream gets ``CANCEL frame_too_large``) and decoding goes on.

Steps 1 to 3 and the fatal case of step 4 raise ``FrameError`` (the caller closes
with 1002 and the reason). An unknown type is skipped and counted.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any

PROTOCOL = 1
HEADER = struct.Struct(">BIBI")
HEADER_BYTES = HEADER.size  # 10

HELLO = 0x01
REQ_HEAD = 0x02
REQ_BODY = 0x03
RESP_HEAD = 0x04
RESP_BODY = 0x05
WS_OPEN = 0x06
WS_ACCEPT = 0x07
WS_MSG = 0x08
WS_CLOSE = 0x09
CANCEL = 0x0A
CREDIT = 0x0B
CONTROL = 0x0C

TYPES: dict[str, int] = {
    "HELLO": HELLO,
    "REQ_HEAD": REQ_HEAD,
    "REQ_BODY": REQ_BODY,
    "RESP_HEAD": RESP_HEAD,
    "RESP_BODY": RESP_BODY,
    "WS_OPEN": WS_OPEN,
    "WS_ACCEPT": WS_ACCEPT,
    "WS_MSG": WS_MSG,
    "WS_CLOSE": WS_CLOSE,
    "CANCEL": CANCEL,
    "CREDIT": CREDIT,
    "CONTROL": CONTROL,
}
JSON_TYPES = frozenset({HELLO, REQ_HEAD, RESP_HEAD, WS_OPEN, WS_ACCEPT, CONTROL})
CHUNK_TYPES = frozenset({REQ_BODY, RESP_BODY, WS_MSG})

END = 0x01
BINARY = 0x02
MORE = 0x04
FLAGS: dict[str, int] = {"END": END, "BINARY": BINARY, "MORE": MORE}

# Contract limits (remote-relay.md, "HELLO" and "Frames").
MAX_HEADER_BYTES = 65536
MAX_MESSAGE_BYTES = 1048576
SMALL_PAYLOAD_MAX = 125  # CANCEL and WS_CLOSE
CLOSE_REASON_MAX_BYTES = 123
MAX_CREDIT_WINDOW = 2**31 - 1
MAX_WS_MESSAGE_BYTES = 32 * 1024 * 1024
LIMITS: dict[str, tuple[int, int, int]] = {
    # name: (default, max, floor); initial_credit is also raised to max_chunk.
    "max_streams": (64, 64, 1),
    "max_chunk": (262144, 262144, 16384),
    "initial_credit": (1048576, 16777216, 16384),
}
DEFAULT_MAX_CHUNK = LIMITS["max_chunk"][0]


class FrameError(Exception):
    """A connection-fatal decoding error; ``reason`` is the close reason (1002)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Frame:
    type: int
    stream_id: int
    flags: int
    payload: bytes

    def has(self, flag: int) -> bool:
        return bool(self.flags & flag)

    def json(self) -> Any:
        """The JSON payload, or None when it does not parse."""
        try:
            return json.loads(self.payload)
        except (ValueError, UnicodeDecodeError):
            return None


@dataclass
class Decoded:
    frames: list[Frame] = field(default_factory=list)
    # Odd streams that sent an over-limit frame: each gets CANCEL frame_too_large.
    too_large: list[int] = field(default_factory=list)
    unknown_types: int = 0


def type_limit(frame_type: int, max_chunk: int) -> int | None:
    """The payload limit of a known type, None for an unknown one."""
    if frame_type in JSON_TYPES:
        return MAX_HEADER_BYTES
    if frame_type in CHUNK_TYPES:
        return max_chunk
    if frame_type == CREDIT:
        return 4
    if frame_type in (CANCEL, WS_CLOSE):
        return SMALL_PAYLOAD_MAX
    return None


def decode_message(data: bytes, *, max_chunk: int = DEFAULT_MAX_CHUNK) -> Decoded:
    """Every frame of one binary message, in order (see the module docstring)."""
    out = Decoded()
    if not data:
        raise FrameError("frame_truncated")
    view = memoryview(data)
    offset = 0
    while offset < len(data):
        if len(data) - offset < HEADER_BYTES:
            raise FrameError("frame_truncated")
        frame_type, stream_id, flags, length = HEADER.unpack_from(view, offset)
        offset += HEADER_BYTES
        if frame_type == 0:
            raise FrameError("frame_type_invalid")
        if len(data) - offset < length:
            raise FrameError("frame_truncated")
        start = offset
        offset += length
        limit = type_limit(frame_type, max_chunk)
        if limit is None:
            out.unknown_types += 1
            continue
        if length > limit:
            if stream_id == 0 or stream_id % 2 == 0:
                raise FrameError("frame_too_large")
            out.too_large.append(stream_id)
            continue
        out.frames.append(Frame(frame_type, stream_id, flags, bytes(view[start:offset])))
    return out


def encode_frame(frame_type: int, stream_id: int, flags: int = 0, payload: bytes = b"") -> bytes:
    return HEADER.pack(frame_type, stream_id, flags, len(payload)) + payload


def json_payload(value: Any) -> bytes:
    """The wire JSON: compact and ASCII-escaped, as the vectors are."""
    return json.dumps(value, separators=(",", ":")).encode()


def encode_json(frame_type: int, stream_id: int, value: Any, flags: int = 0) -> bytes:
    return encode_frame(frame_type, stream_id, flags, json_payload(value))


def close_payload(code: int, reason: str = "") -> bytes:
    """A ``WS_CLOSE`` payload; 1005 (no status) is the empty payload."""
    if code == 1005:
        return b""
    # At most 123 bytes, never cutting a UTF-8 sequence in half.
    raw = reason.encode()[:CLOSE_REASON_MAX_BYTES].decode("utf-8", "ignore").encode()
    return struct.pack(">H", code) + raw


def parse_close(payload: bytes) -> tuple[int, str]:
    """``(code, reason)`` of a ``WS_CLOSE``; an empty payload is 1005."""
    if len(payload) < 2:
        return 1005, ""
    (code,) = struct.unpack_from(">H", payload)
    return code, payload[2:].decode("utf-8", "replace")


def credit_payload(increment: int) -> bytes:
    return struct.pack(">I", increment)


def parse_credit(payload: bytes) -> int | None:
    """The increment of a ``CREDIT``; None when malformed or zero."""
    if len(payload) != 4:
        return None
    (increment,) = struct.unpack(">I", payload)
    return increment or None


def effective_limits(relay: Any) -> dict[str, int]:
    """The relay's effective limits, clamped to the contract (max, then floor)."""
    limits = relay if isinstance(relay, dict) else {}
    out: dict[str, int] = {}
    for name, (default, maximum, floor) in LIMITS.items():
        value = limits.get(name, default)
        if not isinstance(value, int) or isinstance(value, bool):
            value = default
        out[name] = max(floor, min(value, maximum))
    out["initial_credit"] = max(out["initial_credit"], out["max_chunk"])
    return out
