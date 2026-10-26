"""Watched nodes: ``contact.node_seen`` and ``contact.node_back`` (MSHL-129).

``app_settings.notification_rules["watched_nodes"]`` holds at most ``MAX_WATCHED`` entries
``{public_key, channels, cooldown_s, silence_s}``. When an advert (or a decoded direct
message) from a watched key arrives, :func:`observe_sighting` decides:

* ``contact.node_back`` when the previous sighting is at least ``silence_s`` old,
* ``contact.node_seen`` otherwise,

and only when the node's cooldown (``cooldown_s``, 60 s to 30 days) is over: one notification per node and per
cooldown, whichever event it is. A node that is not watched costs one read of the rules and
nothing else.

State stays bounded because only watched keys are ever stored: the last sighting
(``watch:heard:<key>`` in ``notification_state``, the value is the epoch second) and the
cooldown (``dedup:cooldown:watch:<key>``, through ``Dispatcher.claim_cooldown``), so a restart
forgets nothing. Adverts are unauthenticated radio traffic that anyone in range can replay:
the entries, the rows and the notifications are all capped, never driven by the sender.

It is a service called from the contact upsert paths (``packet_processor.py``): the
repository knows nothing about notifications.
"""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from collections.abc import Collection, Mapping
from typing import Any

logger = logging.getLogger(__name__)

MAX_WATCHED = 50
MAX_RULE_CHANNELS = 50
DEFAULT_COOLDOWN_S = 3600
DEFAULT_SILENCE_S = 86_400
MIN_COOLDOWN_S = 60  # a notification per advert is never wanted: adverts repeat every few minutes
MAX_COOLDOWN_S = 30 * 86_400
MIN_SILENCE_S = 60
MAX_SILENCE_S = 365 * 86_400
# A last sighting is remembered a month past the longest silence a rule can ask for.
HEARD_TTL_S = MAX_SILENCE_S + 30 * 86_400

HEARD_PREFIX = "watch:heard:"
COOLDOWN_KEY = "watch:"

_KEY = re.compile(r"^[0-9a-f]{64}$")


class WatchedNodeError(ValueError):
    """A watched-node entry that is refused; ``args[0]`` is the offending field."""


# --- validation --------------------------------------------------------------------


def _int_in(value: object, low: int, high: int, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise WatchedNodeError(field)
    return value


def clean_entry(raw: object, *, known_channels: Collection[str] | None = None) -> dict[str, Any]:
    """One entry as stored, or ``WatchedNodeError`` naming the field. The key is lowercased.

    ``known_channels`` (the API; the backup restore checks channels itself) refuses an unknown
    channel id. Web Push (``builtin:push``) is refused, as for keyword rules: it is decided per
    account."""
    from app.notifications.channels import BUILTIN_PUSH_ID

    if not isinstance(raw, Mapping):
        raise WatchedNodeError("watched_nodes")
    key = raw.get("public_key")
    if not isinstance(key, str) or not _KEY.match(key.strip().lower()):
        raise WatchedNodeError("public_key")
    channels = raw.get("channels", [])
    if (
        not isinstance(channels, list)
        or len(channels) > MAX_RULE_CHANNELS
        or not all(isinstance(c, str) and c and c != BUILTIN_PUSH_ID for c in channels)
    ):
        raise WatchedNodeError("channels")
    channels = list(dict.fromkeys(channels))
    if known_channels is not None and any(c not in known_channels for c in channels):
        raise WatchedNodeError("channels")
    return {
        "public_key": key.strip().lower(),
        "channels": channels,
        "cooldown_s": _int_in(
            raw.get("cooldown_s", DEFAULT_COOLDOWN_S),
            MIN_COOLDOWN_S,
            MAX_COOLDOWN_S,
            "cooldown_s",
        ),
        "silence_s": _int_in(
            raw.get("silence_s", DEFAULT_SILENCE_S), MIN_SILENCE_S, MAX_SILENCE_S, "silence_s"
        ),
    }


def clean_entries(
    raw: object, *, known_channels: Collection[str] | None = None
) -> list[dict[str, Any]]:
    """The whole list: at most ``MAX_WATCHED`` entries, one per key."""
    if not isinstance(raw, list) or len(raw) > MAX_WATCHED:
        raise WatchedNodeError("watched_nodes")
    entries = [clean_entry(e, known_channels=known_channels) for e in raw]
    if len({e["public_key"] for e in entries}) != len(entries):
        raise WatchedNodeError("public_key")
    return entries


def effective_cooldown(value: object) -> int:
    """The cooldown a stored value stands for: legacy rows (below the minimum, ``0``, not an
    integer) behave as ``MIN_COOLDOWN_S``, so an advert never notifies each time (MSHL-401)."""
    if not isinstance(value, int) or isinstance(value, bool):
        return MIN_COOLDOWN_S
    return min(max(value, MIN_COOLDOWN_S), MAX_COOLDOWN_S)


def normalize_stored(stored: object) -> list[Any]:
    """The stored list as the API returns it (an absent ``cooldown_s`` stays absent, so the
    default applies as before): rows written before the minimum existed get a
    valid ``cooldown_s``, so the client can send the list back unchanged."""
    if not isinstance(stored, list):
        return []
    return [
        {**e, "cooldown_s": effective_cooldown(e["cooldown_s"])}
        if isinstance(e, Mapping) and "cooldown_s" in e
        else e
        for e in stored
    ]


def watched_entry(rules: Mapping[str, Any], public_key: str) -> Mapping[str, Any] | None:
    """The stored entry for ``public_key``, if any. Never raises."""
    stored = rules.get("watched_nodes")
    if not isinstance(stored, list):
        return None
    key = public_key.lower()
    for entry in stored[:MAX_WATCHED]:
        if isinstance(entry, Mapping) and str(entry.get("public_key") or "").lower() == key:
            return entry
    return None


# --- state -------------------------------------------------------------------------


async def forget(public_keys: Collection[str]) -> None:
    """Drop the stored sighting and cooldown of keys that are no longer watched."""
    from app.repository.notification_channels import NotificationJournalRepository as Journal

    for key in public_keys:
        try:
            await Journal.state_delete(f"{HEARD_PREFIX}{key.lower()}")
            await Journal.state_delete(f"dedup:cooldown:{COOLDOWN_KEY}{key.lower()}")
        except Exception:
            logger.debug("watched node state not cleared", exc_info=True)
    if public_keys:
        from app.notifications.dispatcher import notification_dispatcher

        notification_dispatcher().forget_cooldowns(
            f"{COOLDOWN_KEY}{k.lower()}" for k in public_keys
        )


async def _previous_sighting(public_key: str, now: int) -> int | None:
    from app.repository.notification_channels import NotificationJournalRepository as Journal

    value = await Journal.state_get(f"{HEARD_PREFIX}{public_key}", now)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


async def _record_sighting(public_key: str, now: int) -> None:
    from app.repository.notification_channels import NotificationJournalRepository as Journal

    await Journal.state_set(f"{HEARD_PREFIX}{public_key}", str(now), now + HEARD_TTL_S)


# --- the service -------------------------------------------------------------------


_UNSTORED_SEEN: OrderedDict[tuple[str, int, str], None] = OrderedDict()
_UNSTORED_SEEN_MAX = 256


def claim_unstored_dm(public_key: str, sender_timestamp: int, text: str) -> bool:
    """True the first time a DM that is decoded but never stored (a repeater's reply, a CLI
    response) is seen. Stored DMs are deduplicated by the message table; these have no row, so a
    small bounded memory of recent ones stops several receptions, or both ingest paths, counting
    one DM twice (MSHL-408)."""
    ident = (public_key.lower(), int(sender_timestamp), text)
    if ident in _UNSTORED_SEEN:
        _UNSTORED_SEEN.move_to_end(ident)
        return False
    _UNSTORED_SEEN[ident] = None
    while len(_UNSTORED_SEEN) > _UNSTORED_SEEN_MAX:
        _UNSTORED_SEEN.popitem(last=False)
    return True


async def observe_sighting(
    public_key: str,
    *,
    name: str = "",
    contact_type: int = 0,
    previous_seen: int | None = None,
    now: int | None = None,
) -> None:
    """A node was just heard. Emits ``contact.node_seen`` / ``contact.node_back`` when it is
    watched and its cooldown is over. Never raises.

    ``previous_seen`` is the contact's ``last_seen`` before this sighting, used only when the
    node has no stored sighting yet (a node watched after it was last heard)."""
    try:
        await _observe(
            public_key.lower(),
            name=name,
            contact_type=contact_type,
            previous_seen=previous_seen,
            now=int(time.time()) if now is None else now,
        )
    except Exception:
        logger.debug("watched node notification failed", exc_info=True)


async def _observe(
    public_key: str, *, name: str, contact_type: int, previous_seen: int | None, now: int
) -> None:
    from app.notifications import rules as rules_mod

    rules = await rules_mod.load_rules()
    entry = watched_entry(rules, public_key)
    if entry is None:
        return  # not watched: nothing stored, nothing emitted
    previous = await _previous_sighting(public_key, now)
    if previous is None:
        previous = previous_seen if previous_seen and previous_seen > 0 else None
    await _record_sighting(public_key, now)

    silence = entry.get("silence_s")
    silence = silence if isinstance(silence, int) and silence > 0 else DEFAULT_SILENCE_S
    silent_s = now - previous if previous is not None else 0
    back = previous is not None and silent_s >= silence

    ids = entry.get("channels")
    from app.notifications.channels import BUILTIN_PUSH_ID

    targets = [
        c
        for c in (ids if isinstance(ids, list) else [])
        if isinstance(c, str) and c != BUILTIN_PUSH_ID
    ]
    if not targets:
        return
    from app.notifications.dispatcher import emit, notification_dispatcher
    from app.notifications.events import event_from_payload

    if not await notification_dispatcher().claim_cooldown(
        (
            f"{COOLDOWN_KEY}{public_key}",
            effective_cooldown(entry.get("cooldown_s", DEFAULT_COOLDOWN_S)),
        )
    ):
        return
    if not name:
        name, contact_type = await _contact_identity(public_key, contact_type)
    payload = {
        "event": "node_back" if back else "node_seen",
        "public_key": public_key,
        "name": name,
        "contact_type": contact_type,
        "silent_s": silent_s if back else 0,
        "heard_at": now,
    }
    event = event_from_payload(payload)
    if event is not None:
        await emit(event, payload=payload, targets=targets, rules=rules)


async def _contact_identity(public_key: str, contact_type: int) -> tuple[str, int]:
    from app.repository.contacts import ContactRepository

    contact = await ContactRepository.get_by_key(public_key)
    if contact is None:
        return "", contact_type
    return contact.name or "", contact.type or contact_type
