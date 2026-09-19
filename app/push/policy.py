"""Pure conversation-enablement policy for Web Push.

Mute is a separate manager-level circuit breaker and is not evaluated here.
The frontend Vague C hook must mirror these cases exactly.
"""

from collections.abc import Mapping

from app.repository.settings import PushDefaults, media_enabled


def conversation_is_enabled(
    *,
    state_key: str,
    message_type: str,
    defaults: PushDefaults,
    overrides: Mapping[str, bool],
    is_hashtag: bool = False,
    is_public: bool = False,
    contact_type: int | None = None,
    channel: str = "push",
) -> bool:
    """Return whether a conversation should receive a notification.

    Precedence: explicit override (all media) > PRIV (DM and rooms) via
    ``new_dm`` for the requested medium > Public or hashtag ON for push
    only > private channel OFF.

    There is no channel-message row in the notification matrix, so
    non-push media stay off for CHAN unless an override forces the
    conversation on.

    ``contact_type`` is unused for enablement (rooms are PRIV). Kept so
    callers can pass it without a second signature.
    """
    _ = contact_type
    if state_key in overrides:
        return bool(overrides[state_key])
    if message_type == "PRIV":
        return media_enabled(defaults, "new_dm", channel)
    if channel != "push":
        return False
    return bool(is_public or is_hashtag)
