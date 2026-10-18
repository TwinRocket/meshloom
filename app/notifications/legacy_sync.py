"""The 4.18.x settings as channels and rules, kept in step both ways of writing.

Until the producers read the rules (MSHL-112), the delivery source is still
``push_defaults``, ``push_conversation_overrides`` and ``notification_destinations``: the
producers compute their medium flags from them and the ``legacy:*`` transports read the
destinations at send time. The deprecated routes (``/api/push/preferences*``,
``PATCH /api/settings`` with ``notification_destinations``) therefore keep writing those
columns, and the repository calls the two functions below after every such write, so the
channels and the rules the new API shows say the same thing:

- ``sync_destinations()`` upserts ``legacy:email|webhook|apprise`` from the destinations
  (a destination that is no longer configured deletes its channel, with the cascade);
- ``sync_rules()`` sets, in ``notification_rules``, which of ``builtin:push`` and the
  ``legacy:*`` channels each event and each conversation reaches. The other channels in
  those rules, ``message.channel`` and every other rule key are left as they are.

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
