"""Deprecated: the 4.18.x conversation policy, kept for one release.

The policy now lives in ``app.notifications.rules.conversation_channel_enabled``, over N
channels and the ``notification_rules`` document. This adapter turns the legacy
``push_defaults`` / ``push_conversation_overrides`` shapes into rules and asks that function;
it is removed with the shims in 5.2.0.
"""

from collections.abc import Mapping
from typing import Any

from app.migrations._113_notification_channels import MEDIUM_CHANNEL, build_rules
from app.notifications.rules import conversation_channel_enabled
from app.repository.settings import PushDefaults


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
    """Whether a conversation notifies the medium ``channel`` (``push``, ``email``, ...).

    ``contact_type`` is unused (rooms are PRIV); kept so callers need no second signature.
    """
    _ = contact_type
    rules = build_rules(defaults, overrides, set(MEDIUM_CHANNEL.values()))
    return conversation_channel_enabled(
        state_key=state_key,
        message_type=message_type,
        rules=rules,
        channel_id=MEDIUM_CHANNEL[channel],
        is_hashtag=is_hashtag,
        is_public=is_public,
    )
