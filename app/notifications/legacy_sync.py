"""The 4.18.x settings as channels and rules, kept in step both ways of writing.

The producers emit events and the rules decide the delivery (``app/notifications/producers.py``);
the ``legacy:*`` transports still read ``notification_destinations`` at send time. The
deprecated routes (``/api/push/preferences*``, ``PATCH /api/settings`` with
``notification_destinations``) keep writing ``push_defaults``, ``push_conversation_overrides``
and the destinations, and the repository calls the two functions below after every such write,
so the channels and the rules the new API shows say the same thing:

- ``sync_destinations()`` upserts ``legacy:email|webhook|apprise`` from the destinations
  (a destination that is no longer configured deletes its channel, with the cascade);
- ``sync_rules()`` sets, in ``notification_rules``, which of ``builtin:push`` and the
  ``legacy:*`` channels each event and each conversation reaches. The other channels in
  those rules, ``message.channel`` and every other rule key are left as they are.

The other way, ``sync_columns()`` copies the legacy media of the rules into the two preference
columns after the new API changed them, so the deprecated routes show what is delivered.

Never raises into the writer: a failure is logged and the next write repairs it.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from typing import Any

from app.migrations import _113_notification_channels as import_113

logger = logging.getLogger(__name__)

MEDIUM_IDS = frozenset(import_113.MEDIUM_CHANNEL.values())
LEGACY_IDS = ("legacy:email", "legacy:webhook", "legacy:apprise")
# ``message.channel`` has no legacy flag (a Public or # message reached Web Push only).
SKIPPED_EVENTS = frozenset({"message.channel"})


def _merge_ids(current: object, built: list[str]) -> list[str]:
    """``built`` (the legacy media) followed by the ids of ``current`` that are not one."""
    kept = [c for c in current if c not in MEDIUM_IDS] if isinstance(current, list) else []
    return [*built, *kept]


def merge_rules(current: Mapping[str, Any], built: Mapping[str, Any]) -> dict[str, Any]:
    """``current`` with the legacy media of ``built`` applied. Without rules, ``built``."""
    if not current:
        return dict(built)
    merged = json.loads(json.dumps(current))
    events = merged.setdefault("events", {})
    for event_id, entry in (built.get("events") or {}).items():
        if event_id in SKIPPED_EVENTS:
            continue
        target = events.setdefault(event_id, {})
        target["channels"] = _merge_ids(target.get("channels"), list(entry.get("channels") or []))
        target.setdefault("always_deliver", entry.get("always_deliver", False))
        if "contact_types" in entry:
            types = target.setdefault("contact_types", {})
            for kind, ids in entry["contact_types"].items():
                types[kind] = _merge_ids(types.get(kind), list(ids))
    conversations = merged.setdefault("conversations", {})
    built_conversations = built.get("conversations") or {}
    for key in list(conversations):
        entry = {k: v for k, v in conversations[key].items() if k not in MEDIUM_IDS}
        entry.update(built_conversations.get(key) or {})
        if entry:
            conversations[key] = entry
        else:
            del conversations[key]
    for key, entry in built_conversations.items():
        conversations.setdefault(key, dict(entry))
    merged.setdefault("version", 1)
    return merged


async def sync_rules() -> None:
    """Mirror ``push_defaults`` and ``push_conversation_overrides`` into the rules."""
    from app.repository.notification_channels import NotificationChannelRepository
    from app.repository.settings import AppSettingsRepository

    try:
        defaults = await AppSettingsRepository.get_push_defaults()
        overrides = await AppSettingsRepository.get_push_conversation_overrides()
        ids = {c.id for c in await NotificationChannelRepository.list_all()}
        built = import_113.build_rules(defaults, overrides, ids)
        current = await NotificationChannelRepository.get_rules()
        await NotificationChannelRepository.set_rules(merge_rules(current, built))
    except Exception:
        logger.warning("notification rules not mirrored from the legacy settings", exc_info=True)


async def sync_destinations() -> None:
    """Mirror ``notification_destinations`` into the ``legacy:*`` channels, then the rules."""
    from app.repository.notification_channels import NotificationChannelRepository
    from app.repository.settings import AppSettingsRepository

    try:
        destinations = (await AppSettingsRepository.get()).notification_destinations
        built = [
            c
            for c in import_113.build_channels(destinations.model_dump(), int(time.time()))
            if c["id"] in LEGACY_IDS
        ]
        await NotificationChannelRepository.mirror_legacy(built, LEGACY_IDS)
    except Exception:
        logger.warning("legacy channels not mirrored from the destinations", exc_info=True)
        return
    await sync_rules()


async def sync_columns() -> None:
    """Mirror the four legacy media of the rules back into the 4.18.x preference columns.

    The rules decide the delivery; the deprecated ``/api/push/preferences*`` routes still read
    the columns, so a change made through ``/api/notifications/rules`` is copied there. A
    companion follows ``new_contact`` OR ``advert_companion`` in 4.18.x, so both take the
    companion list. Other channels are not representable and stay in the rules only.
    """
    from app.notifications.rules import _ids
    from app.repository.notification_channels import NotificationChannelRepository
    from app.repository.settings import AppSettingsRepository, or_notification_media

    try:
        rules = await NotificationChannelRepository.get_rules()
        events = rules.get("events")
        if not isinstance(events, Mapping) or not events:
            return
        existing = {c.id for c in await NotificationChannelRepository.list_all()}
        current_defaults = await AppSettingsRepository.get_push_defaults()
        current_overrides = await AppSettingsRepository.get_push_conversation_overrides()
        # The rules hold a legacy medium only while its channel exists (no SMTP, no URL:
        # none). A flag the user set for a medium that is not configured yet is kept as it is.
        known = {m: cid in existing for m, cid in import_113.MEDIUM_CHANNEL.items()}

        def flags(ids: object, current: Mapping[str, Any]) -> dict[str, bool]:
            wanted = set(_ids(ids))
            return {
                m: (cid in wanted) if known[m] else bool(current.get(m, False))
                for m, cid in import_113.MEDIUM_CHANNEL.items()
            }

        defaults: dict[str, Any] = {}
        for key, event in import_113.EVENT_OF_KEY.items():
            defaults[key] = flags((events.get(event) or {}).get("channels"), current_defaults[key])  # type: ignore[literal-required]
        types = ((events.get("contact.first_seen") or {}).get("contact_types")) or {}
        defaults["new_contact"] = defaults["advert_companion"] = flags(
            types.get("companion"),
            or_notification_media(
                current_defaults["new_contact"], current_defaults["advert_companion"]
            ),
        )
        defaults["advert_repeater"] = flags(
            types.get("repeater"), current_defaults["advert_repeater"]
        )
        defaults["advert_sensor"] = flags(types.get("sensor"), current_defaults["advert_sensor"])
        overrides: dict[str, Any] = {}
        conversations = rules.get("conversations")
        for key, entry in (conversations if isinstance(conversations, Mapping) else {}).items():
            stored = {
                m: bool(entry[cid])
                for m, cid in import_113.MEDIUM_CHANNEL.items()
                if known[m] and isinstance(entry, Mapping) and isinstance(entry.get(cid), bool)
            }
            overrides[str(key)] = stored
        for key, entry in current_overrides.items():
            kept = {m: v for m, v in entry.items() if not known.get(m, True)}
            if kept:
                overrides.setdefault(key, {}).update(kept)
        overrides = {k: v for k, v in overrides.items() if v}
        await AppSettingsRepository.write_push_columns(defaults, overrides)
    except Exception:
        logger.warning("legacy preferences not mirrored from the notification rules", exc_info=True)
