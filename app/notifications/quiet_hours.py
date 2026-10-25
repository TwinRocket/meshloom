"""Quiet hours (MSHL-124): the window, the suppressed-event counters and the digest.

``rules.quiet_hours`` = ``{enabled, start, end, weekdays, digest_at_end}`` lives in
``app_settings.notification_rules``. The window is the auto-update one
(``services/update_window.py``: ``HH:MM``, ISO weekdays, an overnight window belongs to the day it
opened, host-local time). This module is pure except for ``resolve_labels``; the dispatcher
(``dispatcher.py``) decides what is suppressed and sends the digest.

Counters live in ``notification_state``, so a restart in the middle of a window keeps them. A
key is ``quiet:<channel>:<event>:<conversation>``: the channel id, the event id and the state key
of a message's conversation (empty for any other event). On ``builtin:push`` a job that carries a
``PushAudience`` is counted once per wanted account, under ``builtin:push@<user id>`` (or
``builtin:push@d`` for the accounts without an override), so the digest reaches only the
accounts that would have had one of the jobs. No text, sender or name is ever stored: names are
read from the channels and contacts tables when the digest is built.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.services.update_window import in_window, normalize_weekdays, parse_hhmm

logger = logging.getLogger(__name__)

DIGEST_EVENT = "quiet_digest"
COUNTER_PREFIX = "quiet:"
ACCOUNT_DEFAULT = "d"
TOP_CONVERSATIONS = 3
NAME_MAX = 40

DEFAULT_QUIET_HOURS: dict[str, Any] = {
    "enabled": False,
    "start": "22:00",
    "end": "07:00",
    "weekdays": [0, 1, 2, 3, 4, 5, 6],
    "digest_at_end": True,
}
_KEYS = frozenset(DEFAULT_QUIET_HOURS)


def validate_quiet_hours(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The normalized ``quiet_hours`` of ``raw`` over the defaults. Raises ``ValueError`` whose
    first argument is the offending field (an unknown key, a bad ``HH:MM``, a weekday outside
    0..6, a non-boolean flag)."""
    merged = {**DEFAULT_QUIET_HOURS, **raw}
    for key in merged:
        if key not in _KEYS:
            raise ValueError(key)
    for flag in ("enabled", "digest_at_end"):
        if not isinstance(merged[flag], bool):
            raise ValueError(flag)
    for clock in ("start", "end"):
        try:
            parse_hhmm(merged[clock])
        except ValueError:
            raise ValueError(clock) from None
    days = merged["weekdays"]
    if not isinstance(days, list | tuple):
        raise ValueError("weekdays")
    try:
        merged["weekdays"] = normalize_weekdays(days)
    except ValueError:
        raise ValueError("weekdays") from None
    merged["start"], merged["end"] = merged["start"].strip(), merged["end"].strip()
    return merged


def quiet_hours_of(rules: Mapping[str, Any]) -> dict[str, Any]:
    """The stored quiet hours, normalized; a missing or malformed value reads as disabled."""
    raw = rules.get("quiet_hours") if isinstance(rules, Mapping) else None
    if not isinstance(raw, Mapping):
        return dict(DEFAULT_QUIET_HOURS)
    try:
        return validate_quiet_hours(raw)
    except ValueError:
        return dict(DEFAULT_QUIET_HOURS)


def is_quiet(rules: Mapping[str, Any], now: float) -> bool:
    """Whether the quiet window is open at ``now`` (epoch seconds, read in host-local time)."""
    config = quiet_hours_of(rules)
    if not config["enabled"]:
        return False
    return in_window(
        datetime.fromtimestamp(now), config["start"], config["end"], config["weekdays"]
    )


# -- counters ---------------------------------------------------------------------


def counter_key(channel_key: str, event: str, conversation: str) -> str:
    return f"{COUNTER_PREFIX}{channel_key}:{event}:{conversation}"


def account_channel_key(channel_id: str, account: int | None) -> str:
    """``builtin:push@12`` for an account, ``builtin:push@d`` for the default group."""
    return f"{channel_id}@{ACCOUNT_DEFAULT if account is None else account}"


def parse_counter_key(key: str) -> tuple[str, str | int | None, str, str] | None:
    """``(channel id, account, event, conversation)`` of a counter key; account is None for a
    counter shared by everyone, ``"d"`` for the default group, else the user id."""
    if not key.startswith(COUNTER_PREFIX):
        return None
    parts = key[len(COUNTER_PREFIX) :].rsplit(":", 2)
    if len(parts) != 3:
        return None
    channel, event, conversation = parts
    account: str | int | None = None
    if "@" in channel:
        channel, _, raw = channel.partition("@")
        if raw == ACCOUNT_DEFAULT:
            account = ACCOUNT_DEFAULT
        elif raw.isdigit():
            account = int(raw)
        else:
            return None
    return channel, account, event, conversation


@dataclass(frozen=True)
class Label:
    """What the digest may say about a conversation."""

    name: str = ""
    is_dm: bool = False
    is_open: bool = False


@dataclass(frozen=True)
class QuietRow:
    event: str
    conversation: str
    count: int


def _clean(name: str) -> str:
    return " ".join(str(name or "").split())[:NAME_MAX]


async def resolve_labels(conversations: Iterable[str]) -> dict[str, Label]:
    """The name and kind of each ``channel-<key>`` / ``contact-<key>`` state key. An unknown
    conversation gets an empty name, so it is only ever counted."""
    from app.channel_constants import is_public_channel_key
    from app.repository.channels import ChannelRepository
    from app.repository.contacts import ContactRepository

    labels: dict[str, Label] = {}
    for state_key in dict.fromkeys(c for c in conversations if c):
        kind, _, key = state_key.partition("-")
        try:
            if kind == "contact":
                contact = await ContactRepository.get_by_key(key)
                labels[state_key] = Label(_clean(contact.name or "") if contact else "", True)
            elif kind == "channel":
                channel = await ChannelRepository.get_by_key(key)
                is_open = is_public_channel_key(key) or bool(channel and channel.is_hashtag)
                name = _clean(channel.name) if channel else ("Public" if is_open else "")
                labels[state_key] = Label(name, False, is_open)
        except Exception:
            logger.debug("quiet digest label unreadable", exc_info=True)
    return labels


def build_digest(
    rows: Iterable[QuietRow],
    labels: Mapping[str, Label],
    *,
    names: bool,
    scope: str = "quiet_hours",
) -> dict[str, Any] | None:
    """The digest payload of ``rows`` (None when there is nothing to say).

    Counts and names only, never a text. A Public or ``#`` channel is always named (its name is
    public, like in the grouped summaries); a DM or a private channel only when ``names`` (the
    channel's text policy is ``always``), else it is summed as ``dms`` / ``private``."""
    named: dict[str, int] = {}
    conversations: set[str] = set()
    messages = contacts = others = dms = private = 0
    for row in rows:
        if row.count <= 0:
            continue
        if row.event.startswith("message."):
            messages += row.count
            conversations.add(row.conversation)
            label = labels.get(row.conversation, Label())
            if label.name and (label.is_open or names):
                named[label.name] = named.get(label.name, 0) + row.count
            elif label.is_dm:
                dms += row.count
            else:
                private += row.count
        elif row.event == "contact.first_seen":
            contacts += row.count
        else:
            others += row.count
    if not (messages or contacts or others):
        return None
    top = sorted(named.items(), key=lambda kv: (-kv[1], kv[0]))
    payload: dict[str, Any] = {"event": DIGEST_EVENT, "scope": scope, "count": messages}
    if messages:
        payload["conversations"] = len(conversations)
    if top:
        payload["items"] = [[name, n] for name, n in top[:TOP_CONVERSATIONS]]
        if len(top) > TOP_CONVERSATIONS:
            payload["more"] = len(top) - TOP_CONVERSATIONS
    for key, value in (("dms", dms), ("private", private), ("contacts", contacts)):
        if value:
            payload[key] = value
    if others:
        payload["others"] = others
    return payload
