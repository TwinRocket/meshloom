"""Server-side message byte limits for every send path.

The numbers are "empirically determined" from the MeshCore firmware's LoRa packet
constraints, and they mirror the web composer
(``frontend/src/components/MessageInput.tsx``). ``tests/test_message_limits.py``
reads the TSX constants with a regular expression and fails when they drift or
cannot be found, so the two sides cannot diverge silently.

Firmware dependency: a direct message carries at most ~156 bytes of text; a channel
message carries ``"<sender name>: <text>"`` in the encrypted payload, so the text
allowance shrinks with the sender name. All sizes are UTF-8 bytes, not characters.
If a firmware release changes the packet budget, change the constants here and in
the composer in the same PR.
"""

from fastapi import HTTPException

DM_HARD_LIMIT = 156  # Max bytes of text for a direct message
CHANNEL_HARD_LIMIT = 156  # Base byte budget of a channel payload, sender prefix included
CHANNEL_SENDER_SEPARATOR_BYTES = 2  # len(": ")
# Assumed sender-name size when the radio name is unknown (same as the composer).
UNKNOWN_SENDER_NAME_BYTES = 10


def byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


def channel_text_allowance(sender_label: str | None) -> int:
    """Max UTF-8 bytes of channel text once the ``"<sender>: "`` prefix is added."""
    name_bytes = byte_length(sender_label) if sender_label else UNKNOWN_SENDER_NAME_BYTES
    return max(1, CHANNEL_HARD_LIMIT - name_bytes - CHANNEL_SENDER_SEPARATOR_BYTES)


def message_too_long_detail(*, limit: int, size: int) -> dict:
    """Coded 400 detail, translated by the UI under errors.message_too_long."""
    return {
        "code": "message_too_long",
        "message": f"Message too long: {size} bytes, the limit is {limit} bytes",
        "params": {"limit": limit, "bytes": size},
    }


def is_message_too_long(exc: HTTPException) -> bool:
    """True when ``exc`` is the coded refusal raised by this module."""
    detail = exc.detail
    return isinstance(detail, dict) and detail.get("code") == "message_too_long"


def _check(text: str, limit: int) -> None:
    size = byte_length(text)
    if size > limit:
        raise HTTPException(status_code=400, detail=message_too_long_detail(limit=limit, size=size))


def ensure_direct_text_fits(text: str) -> None:
    """Raise a coded 400 ``message_too_long`` when a DM exceeds the byte limit."""
    _check(text, DM_HARD_LIMIT)


def ensure_channel_text_fits(text: str, sender_label: str | None) -> None:
    """Raise a coded 400 ``message_too_long`` when a channel text exceeds its allowance."""
    _check(text, channel_text_allowance(sender_label))
