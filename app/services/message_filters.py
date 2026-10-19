"""Block lists and channel mute for the outputs that forward a message to people.

``is_blocked`` is the one implementation of the block-list rule (``blocked_keys``,
``blocked_names``: the rule of the messages listing,
``MessageRepository._build_blocked_incoming_clause``). The notification rules
(``app/notifications/rules.py``) apply it to every notification channel and the Apprise
fanout calls ``is_filtered``; machine integrations (MQTT, SQS, webhook fanout) still get
everything.

The lists are read on every incoming message, so ``block_lists()`` keeps them in memory:
loaded on first use, dropped by ``AppSettingsRepository.update`` (so a restore too) through
``invalidate_block_lists()``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class BlockListed(Protocol):
    """``AppSettings`` and ``BlockLists`` both carry the lists."""

    @property
    def blocked_keys(self) -> Sequence[str]: ...

    @property
    def blocked_names(self) -> Sequence[str]: ...


@dataclass(frozen=True)
class BlockLists:
    blocked_keys: tuple[str, ...] = ()
    blocked_names: tuple[str, ...] = ()


_cached: BlockLists | None = None
_generation = 0


def invalidate_block_lists() -> None:
    """Forget the in-memory lists: the next ``block_lists()`` reads the database."""
    global _cached, _generation
    _generation += 1
    _cached = None


async def block_lists() -> BlockLists:
    """The current block lists, from memory once read."""
    global _cached
    if _cached is not None:
        return _cached
    from app.repository.settings import AppSettingsRepository

    generation = _generation
    settings = await AppSettingsRepository.get()
    lists = BlockLists(tuple(settings.blocked_keys), tuple(settings.blocked_names))
    if generation == _generation:  # no write landed while reading
        _cached = lists
    return lists


def is_blocked(message: Mapping[str, Any], settings: BlockListed) -> bool:
    """Whether an incoming message comes from a blocked key or name."""
    if message.get("outgoing"):
        return False
    blocked_keys = {key.lower() for key in settings.blocked_keys}
    if blocked_keys:
        msg_type = message.get("type")
        if msg_type == "PRIV":
            sender = str(message.get("conversation_key") or "")
        else:
            sender = str(message.get("sender_key") or "")
        if sender and sender.lower() in blocked_keys:
            return True
    sender_name = message.get("sender_name")
    return bool(sender_name) and sender_name in settings.blocked_names


async def is_filtered(message: Mapping[str, Any]) -> bool:
    """``is_blocked``, or a channel message on a muted channel (mute expiry applied)."""
    from app.repository.channels import ChannelRepository

    if is_blocked(message, await block_lists()):
        return True
    key = str(message.get("conversation_key") or "")
    if message.get("type") != "CHAN" or not key:
        return False
    channel = await ChannelRepository.get_by_key(key)
    return channel is not None and channel.muted
