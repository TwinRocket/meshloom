"""Per-account notification rules for Web Push (MSHL-321).

``app_settings.notification_rules`` is the admin defaults: it chooses the channels of every
event for the whole instance. Web Push is the one channel whose recipients are accounts (each
device belongs to one, MSHL-290), so an account may change for its own devices whether an
event reaches them. Every other channel is an instance destination the admin owns and keeps
following the defaults.

An account's override is ``{"version": 1, "events": {event_id: bool}, "contact_types":
{kind: bool}, "conversations": {state_key: bool}, "pause": {"until": int | None}}``. Absent
means "follow the defaults"; ``true`` puts ``builtin:push`` on that event (or contact type) for
the account, ``false`` takes it off and wins over everything, the admin's conversation
exceptions included. A ``true`` still goes through the admin's conversation exceptions, mutes,
block lists and pauses, which belong to the instance.

``conversations`` (MSHL-362) is the account's own exception for one conversation
(``channel-<key>`` or ``contact-<pubkey>``, the key of ``rules.py``): it is more specific than an
event choice, so it wins over it, both ways. ``false`` keeps every ``message.*`` event of that
conversation (mentions included) away from the account's devices; ``true`` lists
``builtin:push`` on it as an admin exception would (a muted conversation or a blocked sender
still cuts it, unless the event is ``always_deliver``).

``pause`` (MSHL-363) holds every event for the account's devices until ``until`` (epoch
seconds, ``None`` until the account resumes), except urgent, ``always_deliver`` and locked events, like
the instance pause. It never touches the other accounts nor an instance channel. The functions are
pure over the rules; the dispatcher reads the stored overrides (``UserNotificationRulesRepository``)
and hands the resulting ``PushAudience`` to the Web Push transport.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.notifications.channels import BUILTIN_PUSH_ID
from app.notifications.events import EVENT_IDS, FirstSeenEvent, NotificationEvent
from app.notifications.rules import MENTION_EVENT, always_deliver, event_channel_ids

PUSH_ID = BUILTIN_PUSH_ID
CONTACT_KINDS: dict[int, str] = {1: "companion", 2: "repeater", 4: "sensor"}
CONTACT_KIND_NAMES = tuple(CONTACT_KINDS.values())
# Events no account can switch off: the proof of a change to the notification destinations
# (``notify.channel_changed``, ``notify.destination_changed``), the notice of a failing channel
# (``notify.channel_failed``), the heartbeat of a channel (``notify.heartbeat``, MSHL-131),
# and the test button.
LOCKED_EVENTS = frozenset(
    {
        "notify.channel_changed",
        "notify.destination_changed",
        "notify.channel_failed",
        "notify.heartbeat",
        "notify.test",
    }
)
# Operational notices about the instance's notification setup (MSHL-399), and the heartbeat
# of a channel (MSHL-407): on Web Push they reach only the devices of administrators, as
# ``/api/health`` keeps channel health for them and the ``notification_event`` frame of a
# ``notify.*`` event reaches admin sockets only. Fail closed: a device whose owner's role is
# unknown never gets one. ``notify.test`` on Web Push goes to the requester's devices only
# (``requester_audience``).
ADMIN_ONLY_EVENTS = frozenset(
    {
        "notify.channel_changed",
        "notify.channel_failed",
        "notify.destination_changed",
        "notify.heartbeat",
    }
)
OVERRIDABLE_EVENTS: tuple[str, ...] = tuple(e for e in EVENT_IDS if e not in LOCKED_EVENTS)
FIRST_SEEN_EVENT = "contact.first_seen"
# ``channel-<key>`` / ``contact-<pubkey>``: the state key of ``rules.py`` (hex keys).
CONVERSATION_KEY = re.compile(r"^(?:channel|contact)-[0-9A-Fa-f]{1,128}$")
MAX_CONVERSATIONS = 256
# A pause is at most a year long: anything further is a typo, not a plan.
MAX_PAUSE_S = 366 * 24 * 3600


def sanitize_override(raw: object) -> dict[str, Any]:
    """The override a stored or submitted document may hold: known events and contact types
    with boolean values, nothing else. ``{}`` when nothing is left."""
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Any] = {}
    events = raw.get("events")
    kept_events = {
        k: v
        for k, v in (events.items() if isinstance(events, Mapping) else [])
        if k in OVERRIDABLE_EVENTS and isinstance(v, bool)
    }
    if kept_events:
        out["events"] = kept_events
    types = raw.get("contact_types")
    kept_types = {
        k: v
        for k, v in (types.items() if isinstance(types, Mapping) else [])
        if k in CONTACT_KIND_NAMES and isinstance(v, bool)
    }
    if kept_types:
        out["contact_types"] = kept_types
    conversations = raw.get("conversations")
    kept_conversations = {
        k: v
        for k, v in (conversations.items() if isinstance(conversations, Mapping) else [])
        if isinstance(k, str) and CONVERSATION_KEY.match(k) and isinstance(v, bool)
    }
    if kept_conversations:
        out["conversations"] = dict(list(kept_conversations.items())[:MAX_CONVERSATIONS])
    pause = raw.get("pause")
    if isinstance(pause, Mapping):
        until = pause.get("until")
        if until is None:
            out["pause"] = {"until": None}
        elif isinstance(until, int) and not isinstance(until, bool) and until > 0:
            out["pause"] = {"until": until}
    if out:
        out["version"] = 1
    return out


def pause_until(override: Mapping[str, Any]) -> int | None:
    """The end of the account's pause (epoch seconds), None when it has none or it never ends
    (``is_paused`` tells those apart)."""
    pause = override.get("pause")
    until = pause.get("until") if isinstance(pause, Mapping) else None
    return until if isinstance(until, int) and not isinstance(until, bool) else None


def is_paused(override: Mapping[str, Any], now: float | None = None) -> bool:
    """Whether the account's pause holds at ``now``: set, and not over yet."""
    pause = override.get("pause")
    if not isinstance(pause, Mapping):
        return False
    until = pause_until(override)
    return until is None or until > (time.time() if now is None else now)


def conversation_state_key(event: NotificationEvent) -> str | None:
    """The state key of a ``message.*`` event's conversation (None for any other event),
    built as ``rules.event_channel_ids`` does."""
    if not event.event.startswith("message.") or not event.conversation_key:
        return None
    is_dm = event.to_payload().get("type") == "PRIV"
    return f"{'contact' if is_dm else 'channel'}-{event.conversation_key}"


def _conversation_choice(override: Mapping[str, Any], event: NotificationEvent) -> bool | None:
    key = conversation_state_key(event)
    conversations = override.get("conversations")
    choice = conversations.get(key) if key and isinstance(conversations, Mapping) else None
    return choice if isinstance(choice, bool) else None


def _with_conversation_push(rules: Mapping[str, Any], state_key: str) -> dict[str, Any]:
    """A copy of ``rules`` with a stored ``true`` exception for ``builtin:push`` on a
    conversation (what an admin exception is)."""
    conversations = dict(rules.get("conversations") or {})
    conversations[state_key] = {**dict(conversations.get(state_key) or {}), PUSH_ID: True}
    return {**rules, "conversations": conversations}


def _choice(
    override: Mapping[str, Any], event: NotificationEvent
) -> tuple[bool | None, str | None]:
    """The account's choice for ``event`` (None: follow the defaults) and the contact kind it
    concerns (a first-seen contact has one choice per kind, else the event's own)."""
    events = override.get("events")
    own = events.get(event.event) if isinstance(events, Mapping) else None
    kind: str | None = None
    if isinstance(event, FirstSeenEvent):
        kind = CONTACT_KINDS.get(event.data.contact_type)
        types = override.get("contact_types")
        by_kind = types.get(kind) if isinstance(types, Mapping) and kind else None
        if isinstance(by_kind, bool):
            return by_kind, kind
    return (own if isinstance(own, bool) else None), kind


def _with_push(rules: Mapping[str, Any], event_id: str, kind: str | None) -> dict[str, Any]:
    """A copy of ``rules`` with ``builtin:push`` listed on ``event_id`` (on ``kind`` of its
    ``contact_types`` when it has them)."""
    events = dict(rules.get("events") or {})
    entry = dict(events.get(event_id) or {})
    types = entry.get("contact_types")
    if kind is not None and isinstance(types, Mapping):
        listed = list(types.get(kind) or [])
        entry["contact_types"] = {**types, kind: list(dict.fromkeys([*listed, PUSH_ID]))}
    else:
        listed = list(entry.get("channels") or [])
        entry["channels"] = list(dict.fromkeys([*listed, PUSH_ID]))
    events[event_id] = entry
    return {**rules, "events": events}


def push_wanted(
    rules: Mapping[str, Any],
    override: Mapping[str, Any],
    event: NotificationEvent,
    *,
    is_public_or_hashtag: bool = False,
    muted: bool = False,
    blocked: bool = False,
    now: float | None = None,
) -> bool:
    """Whether ``event`` reaches an account's devices: ``rules`` are the admin defaults and
    ``override`` the account's own choices (``{}`` follows the defaults). Order: the account's
    pause (but urgent and ``always_deliver`` events), its conversation exception, its event
    choice, the defaults."""
    if (
        is_paused(override, now)
        and not event.urgent
        and event.event not in LOCKED_EVENTS
        and not always_deliver(rules, event.event)
    ):
        return False
    conversation = _conversation_choice(override, event)
    if conversation is False:
        return False
    choice, kind = _choice(override, event)
    if choice is False and conversation is None:
        return False
    effective: Mapping[str, Any] = rules
    state_key = conversation_state_key(event)
    if conversation and state_key:
        effective = _with_conversation_push(rules, state_key)
    elif choice:
        effective = _with_push(rules, event.event, kind)
    return PUSH_ID in event_channel_ids(
        effective,
        event,
        is_public_or_hashtag=is_public_or_hashtag,
        muted=muted,
        blocked=blocked,
    )


@dataclass(frozen=True)
class PushAudience:
    """Which payload each account's devices get: ``default`` for an account without an
    override, ``per_user`` for the ones with one. None means "nothing"."""

    default: Mapping[str, Any] | None = None
    per_user: Mapping[int, Mapping[str, Any] | None] = field(default_factory=dict)

    def payload_for(self, user_id: int | None) -> Mapping[str, Any] | None:
        if user_id is not None and user_id in self.per_user:
            return self.per_user[user_id]
        return self.default

    @property
    def empty(self) -> bool:
        return self.default is None and not any(p is not None for p in self.per_user.values())


def is_admin_owner(sub: Mapping[str, Any]) -> bool:
    """Whether a push subscription's owner (``owner_role`` of
    ``PushSubscriptionRepository.deliverable``) is an administrator. False when unknown."""
    return sub.get("owner_role") == "admin"


def admin_audience(admin_ids: Iterable[int], payload: Mapping[str, Any]) -> PushAudience | None:
    """The audience of an ``ADMIN_ONLY_EVENTS`` notice: ``payload`` for each administrator,
    nothing for anyone else (``default`` is None). None when there is no administrator."""
    per_user = dict.fromkeys(admin_ids, payload)
    return PushAudience(default=None, per_user=per_user) if per_user else None


def requester_audience(user_id: int, payload: Mapping[str, Any]) -> PushAudience:
    """The audience of a ``notify.test`` on Web Push (MSHL-407): ``payload`` for the devices
    of the account that pressed **Test**, nothing for anyone else."""
    return PushAudience(default=None, per_user={user_id: payload})


def build_audience(
    rules: Mapping[str, Any],
    overrides: Mapping[int, Mapping[str, Any]],
    event: NotificationEvent,
    payload: Mapping[str, Any],
    *,
    mention: tuple[NotificationEvent, Mapping[str, Any]] | None = None,
    is_public_or_hashtag: bool = False,
    muted: bool = False,
    blocked: bool = False,
    now: float | None = None,
) -> PushAudience:
    """The audience of ``event``. With ``mention`` (a channel message that names the radio:
    its ``message.mention`` event and payload), an account whose rules give it the mention
    gets that payload, else the plain one, never both (the one-notification-per-channel rule
    of ``app/notifications/AGENTS.md``, "Mentions", applied per account)."""

    def resolve(override: Mapping[str, Any]) -> Mapping[str, Any] | None:
        context = {
            "is_public_or_hashtag": is_public_or_hashtag,
            "muted": muted,
            "blocked": blocked,
            "now": now,
        }
        if mention is not None and push_wanted(rules, override, mention[0], **context):
            return mention[1]
        if push_wanted(rules, override, event, **context):
            return payload
        return None

    return PushAudience(
        default=resolve({}), per_user={uid: resolve(ov) for uid, ov in overrides.items()}
    )


def default_state(rules: Mapping[str, Any], event_id: str, kind: str | None = None) -> bool:
    """Whether the admin defaults list ``builtin:push`` on an event (or contact kind): what an
    account sees as "default" in its own settings."""
    entry = (rules.get("events") or {}).get(event_id)
    if not isinstance(entry, Mapping):
        return False
    types = entry.get("contact_types")
    listed = types.get(kind) if kind is not None and isinstance(types, Mapping) else None
    if listed is None:
        listed = entry.get("channels")
    return isinstance(listed, list) and PUSH_ID in listed


__all__ = [
    "ADMIN_ONLY_EVENTS",
    "CONTACT_KIND_NAMES",
    "LOCKED_EVENTS",
    "MENTION_EVENT",
    "OVERRIDABLE_EVENTS",
    "PUSH_ID",
    "PushAudience",
    "admin_audience",
    "build_audience",
    "conversation_state_key",
    "default_state",
    "is_admin_owner",
    "is_paused",
    "pause_until",
    "push_wanted",
    "requester_audience",
    "sanitize_override",
]
