"""Pure conversation-enablement policy for notifications.

Mute is a separate manager-level circuit breaker and is not evaluated here.
The frontend Vague C hook must mirror these cases exactly.
"""

from collections.abc import Mapping
from typing import Any

from app.repository.settings import PushDefaults, coerce_conversation_override, media_enabled


def conversation_is_enabled(
    *,
    state_key: str,
    message_type: str,
    defaults: PushDefaults,
    overrides: Mapping[str, bool | Mapping[str, Any]],
    is_hashtag: bool = False,
    is_public: bool = False,
    contact_type: int | None = None,
    channel: str = "push",
) -> bool:
    """Return whether a conversation should receive a notification.

    The chat-header menu writes per-medium overrides. A stored flag for
    that medium wins; otherwise PRIV follows ``new_dm`` and CHAN is push
    for public/hashtag only.

    ``contact_type`` is unused for enablement (rooms are PRIV). Kept so
    callers can pass it without a second signature.
    """
    _ = contact_type
    stored = coerce_conversation_override(overrides[state_key]) if state_key in overrides else {}
    if channel in stored:
        return bool(stored[channel])
    if message_type == "PRIV":
        return media_enabled(defaults, "new_dm", channel)
    if channel != "push":
        return False
    return bool(is_public or is_hashtag)
