"""Rule evaluation: which channels an event goes to, and which of them are paused.

Pure functions over ``app_settings.notification_rules`` (``app/notifications/AGENTS.md``,
"Channels") plus ``load_rules()``, its one reader. The conversation policy generalises
``app/push/policy.py`` to N channels:

- a stored exception ``conversations[state_key][channel_id]`` wins, either way;
- otherwise a DM follows ``events["message.dm"]``, a Public or ``#`` channel
  ``events["message.channel"]``, and a channel with its own key notifies no one;
- ``contact.first_seen`` follows ``contact_types`` by the contact's type (a room or
  an unknown type never notifies, as in 4.18.x);
- the global pause (``paused_until``) and a channel's own ``paused_until`` hold
  every event except ``always_deliver`` ones and urgent ones.

Block lists and mutes (MSHL-113) are applied by the producer until that ticket
moves them here; the legacy adapter keeps its producers' decisions as they are.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.notifications.events import FirstSeenEvent, NotificationEvent

logger = logging.getLogger(__name__)

# MeshCore contact types (``contacts.type``).
_CONTACT_TYPE_KIND = {1: "companion", 2: "repeater", 4: "sensor"}


@dataclass(frozen=True)
class Target:
    channel_id: str
    paused: bool = False


async def load_rules() -> dict[str, Any]:
    """``app_settings.notification_rules`` (``{}`` when unset or unreadable)."""
    from app.repository.notification_channels import NotificationChannelRepository

    try:
        return await NotificationChannelRepository.get_rules()
    except Exception:
        logger.debug("notification rules unreadable", exc_info=True)
        return {}


def _ids(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _event_rule(rules: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    events = rules.get("events")
    entry = events.get(name) if isinstance(events, Mapping) else None
    return entry if isinstance(entry, Mapping) else {}


def always_deliver(rules: Mapping[str, Any], name: str) -> bool:
    return bool(_event_rule(rules, name).get("always_deliver"))


def message_channel_ids(
    rules: Mapping[str, Any],
    *,
    state_key: str,
    is_dm: bool,
    is_public_or_hashtag: bool,
) -> list[str]:
    """The channels a message reaches (the conversation policy, module doc)."""
    if is_dm:
        defaults = _ids(_event_rule(rules, "message.dm").get("channels"))
    elif is_public_or_hashtag:
        defaults = _ids(_event_rule(rules, "message.channel").get("channels"))
    else:
        defaults = []
    conversations = rules.get("conversations")
    stored = conversations.get(state_key) if isinstance(conversations, Mapping) else None
    exceptions = stored if isinstance(stored, Mapping) else {}
    chosen = [cid for cid in defaults if exceptions.get(cid) is not False]
    chosen += [cid for cid, on in exceptions.items() if on is True and cid not in chosen]
    return chosen


def event_channel_ids(
    rules: Mapping[str, Any],
    event: NotificationEvent,
    *,
    is_public_or_hashtag: bool = False,
) -> list[str]:
    """The channels ``event`` reaches before pauses and channel state."""
    if event.event.startswith("message."):
        payload = event.to_payload()
        is_dm = payload.get("type") == "PRIV"
        state_key = f"{'contact' if is_dm else 'channel'}-{event.conversation_key or ''}"
        return message_channel_ids(
            rules,
            state_key=state_key,
            is_dm=is_dm,
            is_public_or_hashtag=is_public_or_hashtag,
        )
    entry = _event_rule(rules, event.event)
    if isinstance(event, FirstSeenEvent):
        kind = _CONTACT_TYPE_KIND.get(event.data.contact_type)
        types = entry.get("contact_types")
        if kind is None:
            return []
        if isinstance(types, Mapping):
            return _ids(types.get(kind))
    return _ids(entry.get("channels"))


def globally_paused(rules: Mapping[str, Any], now: float) -> bool:
    until = rules.get("paused_until")
    return isinstance(until, int | float) and until > now


def apply_pauses(
    rules: Mapping[str, Any],
    channel_ids: list[str],
    *,
    name: str,
    urgent: bool,
    channel_paused_until: Mapping[str, int | None],
    now: float,
) -> list[Target]:
    """Mark each target paused when the global or its own pause holds this event."""
    exempt = urgent or always_deliver(rules, name)
    paused_all = not exempt and globally_paused(rules, now)
    targets: list[Target] = []
    for channel_id in dict.fromkeys(channel_ids):
        own = channel_paused_until.get(channel_id)
        paused = paused_all or (not exempt and own is not None and own > now)
        targets.append(Target(channel_id, paused=paused))
    return targets
