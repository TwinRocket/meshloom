"""Per-account notification rules for Web Push (MSHL-321).

``app_settings.notification_rules`` is the admin defaults: it chooses the channels of every
event for the whole instance. Web Push is the one channel whose recipients are accounts (each
device belongs to one, MSHL-290), so an account may change for its own devices whether an
event reaches them. Every other channel is an instance destination the admin owns and keeps
following the defaults.

An account's override is ``{"version": 1, "events": {event_id: bool}, "contact_types":
{kind: bool}}``. Absent means "follow the defaults"; ``true`` puts ``builtin:push`` on that
event (or contact type) for the account, ``false`` takes it off and wins over everything,
conversation exceptions included. A ``true`` still goes through the admin's conversation
exceptions, mutes, block lists and pauses, which belong to the instance. The functions are
pure over the rules; the dispatcher reads the stored overrides (``UserNotificationRulesRepository``)
and hands the resulting ``PushAudience`` to the Web Push transport.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.notifications.channels import BUILTIN_PUSH_ID
from app.notifications.events import EVENT_IDS, FirstSeenEvent, NotificationEvent
from app.notifications.rules import MENTION_EVENT, event_channel_ids

PUSH_ID = BUILTIN_PUSH_ID
CONTACT_KINDS: dict[int, str] = {1: "companion", 2: "repeater", 4: "sensor"}
CONTACT_KIND_NAMES = tuple(CONTACT_KINDS.values())
# Events no account can switch off: the proof of a change to the notification destinations
# (``notify.channel_changed``, ``notify.destination_changed``), and the test button.
LOCKED_EVENTS = frozenset({"notify.channel_changed", "notify.destination_changed", "notify.test"})
OVERRIDABLE_EVENTS: tuple[str, ...] = tuple(e for e in EVENT_IDS if e not in LOCKED_EVENTS)
FIRST_SEEN_EVENT = "contact.first_seen"


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
    if out:
        out["version"] = 1
    return out


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
) -> bool:
    """Whether ``event`` reaches an account's devices: ``rules`` are the admin defaults and
    ``override`` the account's own choices (``{}`` follows the defaults)."""
    choice, kind = _choice(override, event)
    if choice is False:
        return False
    effective = _with_push(rules, event.event, kind) if choice else rules
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
    "CONTACT_KIND_NAMES",
    "LOCKED_EVENTS",
    "MENTION_EVENT",
    "OVERRIDABLE_EVENTS",
    "PUSH_ID",
    "PushAudience",
    "build_audience",
    "default_state",
    "push_wanted",
    "sanitize_override",
]
