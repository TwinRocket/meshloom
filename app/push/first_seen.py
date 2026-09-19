"""First-seen contact push (out-of-band, not a WebSocket event)."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from app.models import Contact
from app.repository.contacts import ContactRepository
from app.repository.settings import AppSettingsRepository
from app.services.radio_runtime import radio_runtime

logger = logging.getLogger(__name__)

FirstSeenOrigin = Literal["rf_advert", "radio_event", "manual"]

_ALLOWED_ORIGINS: frozenset[str] = frozenset({"rf_advert", "radio_event", "manual"})
_TYPE_UNKNOWN = 0
_TYPE_COMPANION = 1
_TYPE_REPEATER = 2
_TYPE_ROOM = 3
_TYPE_SENSOR = 4


def _contact_mapping(contact: Contact | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(contact, Contact):
        return contact.model_dump()
    return dict(contact)


async def maybe_notify_contact_first_seen(
    contact: Contact | Mapping[str, Any],
    *,
    origin: FirstSeenOrigin,
    promoted_keys: Sequence[str] = (),
) -> None:
    """Notify subscribed devices that a contact row was newly inserted.

    Silent during radio ``post_connect_setup``. Never raises to callers.
    """
    try:
        await _maybe_notify_contact_first_seen(contact, origin=origin, promoted_keys=promoted_keys)
    except Exception:
        logger.debug("First-seen push dispatch failed", exc_info=True)


async def _maybe_notify_contact_first_seen(
    contact: Contact | Mapping[str, Any],
    *,
    origin: FirstSeenOrigin,
    promoted_keys: Sequence[str],
) -> None:
    if origin not in _ALLOWED_ORIGINS:
        return
    if not radio_runtime.is_setup_complete:
        return

    payload = _contact_mapping(contact)
    public_key = str(payload.get("public_key") or "").lower()
    if not public_key:
        return

    contact_type = int(payload.get("type") or 0)
    if contact_type in (_TYPE_UNKNOWN, _TYPE_ROOM):
        return

    if promoted_keys:
        return
    existing_prefixes = await ContactRepository.list_prefix_placeholder_keys(public_key)
    if existing_prefixes:
        return

    from app.repository.settings import any_media_enabled, or_notification_media

    defaults = await AppSettingsRepository.get_push_defaults()
    if contact_type == _TYPE_COMPANION:
        if not (
            any_media_enabled(defaults, "new_contact")
            or any_media_enabled(defaults, "advert_companion")
        ):
            return
        flags = or_notification_media(defaults["new_contact"], defaults["advert_companion"])
    elif contact_type == _TYPE_REPEATER:
        if not any_media_enabled(defaults, "advert_repeater"):
            return
        flags = defaults["advert_repeater"]
    elif contact_type == _TYPE_SENSOR:
        if not any_media_enabled(defaults, "advert_sensor"):
            return
        flags = defaults["advert_sensor"]
    else:
        return

    from app.notify import dispatch_system_event

    await dispatch_system_event(
        {
            "event": "first_seen",
            "public_key": public_key,
            "name": payload.get("name") or "",
            "contact_type": contact_type,
        },
        flags,
    )
