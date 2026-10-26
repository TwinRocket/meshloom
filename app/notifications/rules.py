"""Rule evaluation: which channels an event goes to, and which of them are paused.

Pure functions over ``app_settings.notification_rules`` (``app/notifications/AGENTS.md``,
"Channels") plus ``load_rules()``, its one reader. The conversation policy generalises
``app/push/policy.py`` to N channels:

- a stored exception ``conversations[state_key][channel_id]`` wins, either way;
- otherwise a DM follows ``events["message.dm"]``, a Public or ``#`` channel
  ``events["message.channel"]``, and a channel with its own key notifies no one;
- a mention (``message.mention``, a ``CHAN`` message that names the radio, ``mention.py``)
  replaces the plain notification on the channels of ``events["message.mention"]`` and on the
  channels with a stored ``true`` exception for the conversation; on a channel with its own key it only reaches a non-cloud
  channel or one with a stored exception for that conversation (cadrage Q6: a mention is
  forged by anyone in range, and must not push a private channel's text to a third party);
- ``contact.first_seen`` follows ``contact_types`` by the contact's type (a room or
  an unknown type never notifies, as in 4.18.x);
- Web Push is decided per account over these rules (``user_rules.py``, MSHL-321): the rules
  here are the admin defaults and an account may force an event on or off for its own devices;
- the global pause (``paused_until``) holds every event except ``always_deliver`` ones and
  urgent ones; a channel's own ``paused_until`` (the auto-pause of a channel that keeps
  failing, MSHL-130) holds every event, since the channel cannot deliver them.

Mute and block lists are applied here, for every channel (``muted`` and ``blocked`` of
``event_channel_ids``): the producer (``app/notifications/producers.py``) reads the channel's
effective mute and asks ``message_blocked`` (the one block-list rule, ``message_filters``), and a
muted channel or a blocked sender cuts every ``message.*`` event, stored exceptions included,
unless the event is ``always_deliver``.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from app.notifications.events import FirstSeenEvent, NotificationEvent
from app.services import message_filters

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


def channel_is_cloud(channel: Any) -> bool:
    """Whether a channel sends to a third party, for the private-key mention rule.

    The registry's ``cloud`` flag (``cloud_check`` for a destination the user chooses, such as
    an e-mail relay or ntfy.sh); an Apprise or webhook channel always counts, since its URL is
    a secret that can point at any service (Telegram, Slack, Zapier...) and the public config
    cannot tell. Such a channel gets a private-key mention only through a stored exception.
    """
    from app.notifications.channels import REGISTRY

    entry = REGISTRY.get(channel.type)
    if entry is None or entry.id in ("apprise", "webhook"):
        return True
    return bool(entry.cloud_check(channel.config) if entry.cloud_check else entry.cloud)


async def cloud_channel_ids(candidates: Collection[str]) -> set[str]:
    """The ``candidates`` that are a third party (``channel_is_cloud``).

    Fails closed: a channel that cannot be read or is not stored counts as a third party."""
    from app.notifications.channels import BUILTIN_PUSH_ID
    from app.repository.notification_channels import NotificationChannelRepository

    try:
        known = {c.id: c for c in await NotificationChannelRepository.list_all()}
    except Exception:
        logger.debug("notification channels unreadable", exc_info=True)
        return set(candidates)
    local: set[str] = set()
    for cid in candidates:
        channel = known.get(cid)
        if (
            channel is not None
            and not channel_is_cloud(channel)
            or channel is None
            and cid == BUILTIN_PUSH_ID
        ):
            local.add(cid)
    return {cid for cid in candidates if cid not in local}


async def message_blocked(message: Mapping[str, Any]) -> bool:
    """Whether an incoming message comes from a blocked key or name (block lists in memory).

    Fails closed: lists that cannot be read silence the message rather than let a blocked
    sender through.
    """
    try:
        return message_filters.is_blocked(message, await message_filters.block_lists())
    except Exception:
        logger.debug("block lists unreadable", exc_info=True)
        return True


def _ids(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _event_rule(rules: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    events = rules.get("events")
    entry = events.get(name) if isinstance(events, Mapping) else None
    return entry if isinstance(entry, Mapping) else {}


def always_deliver(rules: Mapping[str, Any], name: str) -> bool:
    return bool(_event_rule(rules, name).get("always_deliver"))


MENTION_EVENT = "message.mention"


def _message_event(message_type: str, *, mention: bool = False) -> str:
    if mention:
        return MENTION_EVENT
    return "message.dm" if message_type == "PRIV" else "message.channel"


def _exceptions(rules: Mapping[str, Any], state_key: str) -> Mapping[str, Any]:
    conversations = rules.get("conversations")
    stored = conversations.get(state_key) if isinstance(conversations, Mapping) else None
    return stored if isinstance(stored, Mapping) else {}


def conversation_channel_enabled(
    *,
    state_key: str,
    message_type: str,
    rules: Mapping[str, Any],
    channel_id: str,
    is_hashtag: bool = False,
    is_public: bool = False,
    muted: bool = False,
    blocked: bool = False,
    now: float | None = None,
    mention: bool = False,
    cloud: bool = False,
) -> bool:
    """Whether a message of this conversation reaches ``channel_id`` (the module doc).

    A stored exception for (conversation, channel) wins; else a DM follows the channels of
    ``message.dm``, a Public or ``#`` channel those of ``message.channel``, a channel with its
    own key none. A muted conversation, a blocked sender and the global pause (when ``now``
    is given) cut everything but an ``always_deliver`` event. Pending channels and outgoing
    messages never reach this point (the producer drops them).

    ``mention`` asks about the ``message.mention`` notification of a channel message instead
    (``cloud``: the channel is a third party, which a private-key channel's mention never
    reaches without a stored exception).
    """
    name = _message_event(message_type, mention=mention)
    if not always_deliver(rules, name) and (
        muted or blocked or (now is not None and globally_paused(rules, now))
    ):
        return False
    listed = channel_id in _ids(_event_rule(rules, name).get("channels"))
    stored = _exceptions(rules, state_key).get(channel_id)
    if mention:
        if message_type == "PRIV":
            return False
        if isinstance(stored, bool):  # an exception wins, listed on the Mentions row or not
            return stored
        return listed and (is_public or is_hashtag or not cloud)
    if isinstance(stored, bool):
        return stored
    if message_type != "PRIV" and not (is_public or is_hashtag):
        return False
    return listed


def candidate_channel_ids(rules: Mapping[str, Any], name: str, state_key: str) -> list[str]:
    """The channels of ``events[name]`` followed by those with a stored exception."""
    candidates = _ids(_event_rule(rules, name).get("channels"))
    return candidates + [cid for cid in _exceptions(rules, state_key) if cid not in candidates]


def message_channel_ids(
    rules: Mapping[str, Any],
    *,
    state_key: str,
    is_dm: bool,
    is_public_or_hashtag: bool,
    muted: bool = False,
    blocked: bool = False,
    mention: bool = False,
    cloud_ids: Collection[str] = (),
) -> list[str]:
    """The channels a message reaches, ``conversation_channel_enabled`` for each candidate.

    With ``mention``, the channels of ``message.mention`` plus those with a stored ``true``
    exception (which also lets a private-key mention through to a third party);
    ``cloud_ids`` are the channels that are a third party."""
    message_type = "PRIV" if is_dm else "CHAN"
    name = _message_event(message_type, mention=mention)
    if mention and is_dm:
        return []
    candidates = candidate_channel_ids(rules, name, state_key)
    return [
        cid
        for cid in candidates
        if conversation_channel_enabled(
            state_key=state_key,
            message_type=message_type,
            rules=rules,
            channel_id=cid,
            is_public=is_public_or_hashtag,
            muted=muted,
            blocked=blocked,
            mention=mention,
            cloud=cid in cloud_ids,
        )
    ]


def event_channel_ids(
    rules: Mapping[str, Any],
    event: NotificationEvent,
    *,
    is_public_or_hashtag: bool = False,
    muted: bool = False,
    blocked: bool = False,
    cloud_ids: Collection[str] = (),
) -> list[str]:
    """The channels ``event`` reaches before pauses and channel state.

    ``muted`` (the conversation's effective mute) and ``blocked`` (the sender is on a block
    list) only concern ``message.*`` events; ``cloud_ids`` (the third-party channels) only
    ``message.mention``."""
    if event.event.startswith("message."):
        payload = event.to_payload()
        is_dm = payload.get("type") == "PRIV"
        state_key = f"{'contact' if is_dm else 'channel'}-{event.conversation_key or ''}"
        return message_channel_ids(
            rules,
            state_key=state_key,
            is_dm=is_dm,
            is_public_or_hashtag=is_public_or_hashtag,
            muted=muted,
            blocked=blocked,
            mention=event.event == MENTION_EVENT,
            cloud_ids=cloud_ids,
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
    """Mark each target paused when the global pause (not for urgent and ``always_deliver``
    events) or its own pause (every event: MSHL-130) holds this event."""
    exempt = urgent or always_deliver(rules, name)
    paused_all = not exempt and globally_paused(rules, now)
    targets: list[Target] = []
    for channel_id in dict.fromkeys(channel_ids):
        own = channel_paused_until.get(channel_id)
        paused = paused_all or (own is not None and own > now)
        targets.append(Target(channel_id, paused=paused))
    return targets
