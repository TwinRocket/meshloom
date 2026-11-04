"""Write a channel to a radio slot, keeping the secret the caller gives.

``mc.commands.set_channel`` replaces the secret of any name starting with ``#`` by
the hash of that name. That is wrong for a private channel named ``#x``, which has a
secret of its own, so every write of a real channel builds the frame here instead.
``set_channel`` stays fine for emptying a slot (empty name, zero secret).
"""

from __future__ import annotations

from typing import Any

from meshcore import EventType

CMD_SET_CHANNEL = 0x20
CHANNEL_SECRET_BYTES = 16
CHANNEL_NAME_FIELD_BYTES = 32
CHANNEL_NAME_MAX_BYTES = 31  # 32-byte field, NUL kept by the firmware


def utf8_prefix(text: str, max_bytes: int) -> bytes:
    """At most ``max_bytes`` of UTF-8, cut on a character boundary."""
    out = bytearray()
    for char in text:
        encoded = char.encode("utf-8")
        if len(out) + len(encoded) > max_bytes:
            break
        out += encoded
    return bytes(out)


def channel_slot_frame(slot: int, name: str, secret: bytes) -> bytes:
    """The SET_CHANNEL frame loading ``name`` and ``secret`` into ``slot``."""
    if len(secret) != CHANNEL_SECRET_BYTES:
        raise ValueError(f"Channel secret must be exactly {CHANNEL_SECRET_BYTES} bytes")
    return (
        bytes([CMD_SET_CHANNEL, slot])
        + utf8_prefix(name, CHANNEL_NAME_MAX_BYTES).ljust(CHANNEL_NAME_FIELD_BYTES, b"\x00")
        + secret
    )


async def write_channel_slot(mc: Any, slot: int, name: str, secret: bytes) -> Any:
    """Load a channel into ``slot``. Returns the radio's event (``OK`` or ``ERROR``)."""
    return await mc.commands.send(
        channel_slot_frame(slot, name, secret), [EventType.OK, EventType.ERROR]
    )
