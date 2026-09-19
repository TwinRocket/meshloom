"""Adopted/pending channel membership and the refused-channel denylist."""

from __future__ import annotations

import json
from typing import Literal

from app.models import Channel, RejectedChannel

ChannelMembership = Literal["adopted", "pending"]
MEMBERSHIP_ADOPTED: ChannelMembership = "adopted"
MEMBERSHIP_PENDING: ChannelMembership = "pending"


def normalize_channel_key(key: str) -> str:
    return (key or "").strip().upper()


def coerce_membership(value: object) -> ChannelMembership:
    if value == MEMBERSHIP_PENDING:
        return MEMBERSHIP_PENDING
    return MEMBERSHIP_ADOPTED


def is_pending_channel(channel: Channel | None) -> bool:
    return channel is not None and channel.membership == MEMBERSHIP_PENDING


def coerce_rejected_channels(raw: object) -> list[RejectedChannel]:
    if raw is None or raw == "":
        return []
    try:
        loaded = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(loaded, list):
        return []
    out: list[RejectedChannel] = []
    seen: set[str] = set()
    for item in loaded:
        if isinstance(item, RejectedChannel):
            key = normalize_channel_key(item.key)
            name = (item.name or "").strip() or key
        elif isinstance(item, dict):
            key = normalize_channel_key(str(item.get("key") or ""))
            name = str(item.get("name") or "").strip() or key
        elif isinstance(item, str):
            key = normalize_channel_key(item)
            name = key
        else:
            continue
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(RejectedChannel(key=key, name=name))
    return out


def rejected_channels_payload(items: list[RejectedChannel]) -> list[dict[str, str]]:
    return [{"key": item.key, "name": item.name} for item in items]


async def adopt_channel_record(
    *,
    key: str,
    name: str,
    is_hashtag: bool = False,
    on_radio: bool = False,
) -> Channel:
    """Force a channel adopted and drop it from the refused denylist."""
    from app.repository.channels import ChannelRepository
    from app.repository.settings import AppSettingsRepository

    key_hex = normalize_channel_key(key)
    if on_radio:
        await ChannelRepository.upsert(
            key=key_hex,
            name=name,
            is_hashtag=is_hashtag,
            on_radio=True,
            membership=MEMBERSHIP_ADOPTED,
        )
    else:
        await ChannelRepository.upsert_name(
            key=key_hex,
            name=name,
            is_hashtag=is_hashtag,
            membership=MEMBERSHIP_ADOPTED,
        )
    await AppSettingsRepository.remove_rejected_channel(key_hex)
    stored = await ChannelRepository.get_by_key(key_hex)
    if stored is None:
        raise RuntimeError(f"Channel {key_hex} was adopted but could not be reloaded")
    return stored
