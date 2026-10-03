"""Shared pending ACK tracking for outgoing direct messages."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

PendingAck = tuple[int, float, int]
BUFFERED_ACK_TTL_SECONDS = 30.0


class AckKey(tuple):
    """Tuple key (radio_id, ack_code) with string-like helpers for backwards compatibility."""

    def __new__(cls, radio_id: str, ack_code: str):
        return super().__new__(cls, (str(radio_id), str(ack_code)))

    @property
    def radio_id(self) -> str:
        return self[0]

    @property
    def ack_code(self) -> str:
        return self[1]

    def startswith(self, prefix: str, *args, **kwargs) -> bool:
        return self[1].startswith(prefix, *args, **kwargs)

    def endswith(self, suffix: str, *args, **kwargs) -> bool:
        return self[1].endswith(suffix, *args, **kwargs)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, tuple) and len(other) == 2:
            return (self[0], self[1]) == (str(other[0]), str(other[1]))
        if isinstance(other, str):
            return self[0] == "default" and self[1] == other
        return super().__eq__(other)

    def __hash__(self) -> int:
        return super().__hash__()

    def __str__(self) -> str:
        return self[1] if self[0] == "default" else f"{self[0]}:{self[1]}"


def _normalize_key(key: Any) -> AckKey:
    if isinstance(key, AckKey):
        return key
    if isinstance(key, tuple) and len(key) == 2:
        return AckKey(str(key[0]), str(key[1]))
    if isinstance(key, str):
        return AckKey("default", key)
    raise TypeError(f"Key must be a string or (radio_id, ack_code) tuple, got {type(key)}")


class RadioScopedAckDict(dict):
    """Dictionary supporting both (radio_id, ack_code) tuples and legacy string keys.

    String keys transparently normalize to ("default", ack_code).
    """

    def __getitem__(self, key: Any) -> Any:
        return super().__getitem__(_normalize_key(key))

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(_normalize_key(key), value)

    def __delitem__(self, key: Any) -> None:
        super().__delitem__(_normalize_key(key))

    def __contains__(self, key: Any) -> bool:
        try:
            norm = _normalize_key(key)
        except TypeError:
            return False
        return super().__contains__(norm)

    def get(self, key: Any, default: Any = None) -> Any:
        try:
            norm = _normalize_key(key)
        except TypeError:
            return default
        return super().get(norm, default)

    def pop(self, key: Any, *args: Any) -> Any:
        norm = _normalize_key(key)
        return super().pop(norm, *args)

    def setdefault(self, key: Any, default: Any = None) -> Any:
        return super().setdefault(_normalize_key(key), default)

    def update(self, *args: Any, **kwargs: Any) -> None:
        if args:
            other = args[0]
            if isinstance(other, dict):
                for k, v in other.items():
                    self[k] = v
            else:
                for k, v in other:
                    self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    def copy(self) -> RadioScopedAckDict:
        new_dict = RadioScopedAckDict()
        new_dict.update(super().copy())
        return new_dict


_pending_acks: RadioScopedAckDict = RadioScopedAckDict()
_buffered_acks: RadioScopedAckDict = RadioScopedAckDict()


def track_pending_ack(
    expected_ack: str,
    message_id: int,
    timeout_ms: int,
    radio_id: str = "default",
) -> bool:
    """Track an expected ACK code for an outgoing direct message on a radio.

    Returns True when the ACK was already observed and buffered before registration.
    """
    key = (radio_id, expected_ack)
    buffered_at = _buffered_acks.pop(key, None)
    if buffered_at is not None:
        logger.debug(
            "[radio:%s] Matched buffered ACK %s immediately for message %d",
            radio_id,
            expected_ack,
            message_id,
        )
        return True

    _pending_acks[key] = (message_id, time.time(), timeout_ms)
    logger.debug(
        "[radio:%s] Tracking pending ACK %s for message %d (timeout %dms)",
        radio_id,
        expected_ack,
        message_id,
        timeout_ms,
    )
    return False


def buffer_unmatched_ack(ack_code: str, radio_id: str = "default") -> None:
    """Remember an ACK that arrived before its message registration."""
    key = (radio_id, ack_code)
    _buffered_acks[key] = time.time()
    logger.debug("[radio:%s] Buffered unmatched ACK %s for late registration", radio_id, ack_code)


def cleanup_expired_acks() -> None:
    """Remove stale pending ACK entries across all radios."""
    now = time.time()
    expired_keys = [
        key
        for key, (_message_id, created_at, timeout_ms) in _pending_acks.items()
        if now - created_at > (timeout_ms / 1000) * 2
    ]
    for key in expired_keys:
        del _pending_acks[key]
        logger.debug("Expired pending ACK %s", key)

    expired_buffered_keys = [
        key
        for key, buffered_at in _buffered_acks.items()
        if now - buffered_at > BUFFERED_ACK_TTL_SECONDS
    ]
    for key in expired_buffered_keys:
        del _buffered_acks[key]
        logger.debug("Expired buffered ACK %s", key)


def pop_pending_ack(ack_code: str, radio_id: str = "default") -> int | None:
    """Claim the tracked message ID for an ACK code if present on the specified radio."""
    key = (radio_id, ack_code)
    pending = _pending_acks.pop(key, None)
    if pending is None:
        return None
    message_id, _, _ = pending
    return message_id


def clear_all() -> None:
    """Drop all pending and buffered ACK state (identity wipe)."""
    _pending_acks.clear()
    _buffered_acks.clear()


def clear_pending_acks_for_message(message_id: int, radio_id: str | None = None) -> None:
    """Remove any still-pending ACK codes for a message once one ACK wins.

    If radio_id is provided, only sibling ACKs for that radio are cleared;
    otherwise sibling ACKs matching message_id across all radios are cleared.
    """
    sibling_keys = [
        key
        for key, (pending_message_id, _created_at, _timeout_ms) in _pending_acks.items()
        if pending_message_id == message_id and (radio_id is None or key[0] == radio_id)
    ]
    for key in sibling_keys:
        del _pending_acks[key]
        logger.debug(
            "[radio:%s] Cleared sibling pending ACK %s for message %d",
            key[0],
            key[1],
            message_id,
        )
