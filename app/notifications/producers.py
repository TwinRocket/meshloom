"""The producers' entry points to the dispatcher.

Each producer decides whether its occurrence is worth reporting (an outgoing message, a
pending channel, a muted one, a first-seen contact during the radio setup...), builds the
legacy payload dict and hands it to ``emit``: the rules (``rules.py``) choose the channels,
the dispatcher queues, limits, retries and journals. Nothing here talks to a medium.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.channel_constants import is_public_channel_key
from app.notifications import keywords as keywords_mod
from app.notifications import rules as rules_mod
from app.notifications.channels import BUILTIN_PUSH_ID
from app.notifications.dispatcher import emit, notification_dispatcher
from app.notifications.events import NotificationEvent, dedup_hash, event_from_payload
from app.notifications.mention import is_mention
from app.notifications.user_rules import PushAudience, build_audience

logger = logging.getLogger(__name__)


async def emit_message(data: Mapping[str, Any]) -> None:
    """``message.dm`` / ``message.channel`` / ``message.mention`` for an incoming message.
    Never raises.

    Outgoing messages and messages of a pending channel never notify; the rules cut a muted
    channel and a blocked sender's messages for every channel (unless the event is
    ``always_deliver``). A channel message that names the radio is a ``message.mention`` on
    the channels of that rule and a plain ``message.channel`` on the others: one notification
    per channel, never both.
    """
    try:
        await _emit_message(data)
    except Exception:
        logger.debug("message notification failed", exc_info=True)


async def _emit_message(data: Mapping[str, Any]) -> None:
    if data.get("outgoing"):
        return
    payload = dict(data)
    payload.setdefault("event", "message")
    payload.pop("mention", None)  # decided here, from the radio name: never taken from the input
    payload.pop("keyword", None)  # likewise decided here, from the keyword rules
    payload.pop("keyword_id", None)
    event = event_from_payload(payload)
    if event is None:
        return
    msg_type = str(payload.get("type") or "")
    key = str(payload.get("conversation_key") or "")
    muted = False
    is_open = False
    if msg_type == "CHAN" and key:
        from app.repository.channels import ChannelRepository

        try:
            channel = await ChannelRepository.get_by_key(key)
        except Exception:
            channel = None
            logger.debug("channel unreadable for a notification", exc_info=True)
        if channel is not None and channel.membership == "pending":
            return
        muted = bool(channel is not None and channel.muted)
        is_open = is_public_channel_key(key) or bool(channel is not None and channel.is_hashtag)
    rules = await rules_mod.load_rules()
    blocked = await rules_mod.message_blocked(payload)
    mentioned: list[str] = []
    push_mention: tuple[NotificationEvent, dict[str, Any]] | None = None
    if msg_type == "CHAN" and is_mention(
        str(payload.get("text") or ""),
        _radio_name(),
        whole_word=bool(rules.get("mention_whole_word")),
    ):
        mention_payload = payload | {"mention": True}
        mention_event = event_from_payload(mention_payload)
        if mention_event is not None:
            # Web Push is decided per account (``user_rules``): the dispatcher gives each
            # account the mention or the plain version from its own rules, so it stays out of
            # the instance-wide split below.
            push_mention = (mention_event, mention_payload)
            mentioned = [
                c
                for c in await _mention_channels(rules, key, is_open, muted, blocked)
                if c != BUILTIN_PUSH_ID
            ]
            if mentioned:
                await emit(
                    mention_event,
                    payload=mention_payload,
                    targets=mentioned,
                    rules=rules,
                    muted=muted,
                    blocked=blocked,
                )
    from app.repository.user_notification_rules import UserNotificationRulesRepository

    candidates = rules_mod.event_channel_ids(
        rules, event, is_public_or_hashtag=is_open, muted=muted, blocked=blocked
    )
    audience = build_audience(
        rules,
        await UserNotificationRulesRepository.all_overrides(),
        event,
        payload,
        mention=push_mention,
        is_public_or_hashtag=is_open,
        muted=muted,
        blocked=blocked,
    )
    # Web Push is per account: who already gets the message by push is the audience, passed on
    # so a keyword alert skips those administrators (MSHL-400), not the whole channel.
    receiving = {c for c in (*candidates, *mentioned) if c != BUILTIN_PUSH_ID}
    if not (muted or blocked):
        await _emit_keywords(
            payload, rules, receiving, is_open, set(mentioned), push_already=audience
        )
    # Nobody to reach: no instance channel left after the mention split, and no account's
    # Web Push wants it.
    if audience.empty and not [
        c for c in candidates if c not in mentioned and c != BUILTIN_PUSH_ID
    ]:
        return
    await emit(
        event,
        payload=payload,
        exclude=mentioned,
        rules=rules,
        is_open=is_open,
        muted=muted,
        blocked=blocked,
        push_mention=push_mention,
    )


async def _digest_channels() -> set[str]:
    """Ids of the channels in digest mode (``digest_interval_s`` > 0)."""
    from app.repository.notification_channels import NotificationChannelRepository

    try:
        return {c.id for c in await NotificationChannelRepository.list_all() if c.digest_interval_s}
    except Exception:
        logger.debug("notification channels unreadable", exc_info=True)
        return set()


async def _emit_keywords(
    payload: Mapping[str, Any],
    rules: Mapping[str, Any],
    receiving: set[str],
    is_open: bool,
    mentioned: frozenset[str] | set[str] = frozenset(),
    *,
    push_already: PushAudience | None = None,
) -> None:
    """``message.keyword`` for each keyword rule the message triggers (``keywords.py``).

    Only on the rule's channels that do not already receive this message (``receiving``: the
    channels of ``message.dm`` / ``message.channel`` / ``message.mention`` for it, so no channel
    gets it twice; a digest-mode channel is not counted as receiving it, MSHL-136). Web Push
    (``builtin:push`` in a rule, MSHL-400) reaches the administrators' devices only, minus those
    ``push_already`` (the message's own push audience) reaches. The caller skips muted
    conversations and blocked senders; the dispatcher
    applies the pauses. A rule fires once per message (dedup key per rule and message) and
    starts its cooldown per rule and per rule and sender, only when it reaches a channel.
    A match is never urgent: the text is forgeable."""
    try:
        hits = keywords_mod.matching_rules(rules, payload)
        if not hits:
            return
        # A channel in digest mode only counts the message in its summary (MSHL-136), so it
        # does not "already receive" it: the keyword alert still reaches it at once. The
        # message stays counted once in the summary; the alert itself is never counted. Read
        # only here, so a message with no keyword hit costs no lookup.
        digest = await _digest_channels()
        receiving = receiving - (digest - mentioned)
        if BUILTIN_PUSH_ID in digest:
            push_already = None  # a digest-mode Web Push only counts the message
        sender = dedup_hash(keywords_mod.sender_id(payload))
        for rule in hits:
            rule_id = str(rule.get("id") or "")
            ids = rule.get("channels")
            targets = [
                c
                for c in (ids if isinstance(ids, list) else [])
                if isinstance(c, str) and c not in receiving
            ]
            if not rule_id or not targets:
                continue
            cooldown = rule.get("cooldown_s")
            if not await notification_dispatcher().claim_cooldown(
                (f"kw:{rule_id}", cooldown if isinstance(cooldown, int) else 0),
                (f"kw:{rule_id}:{sender}", keywords_mod.sender_cooldown_s(rule)),
            ):
                continue
            keyword_payload = {
                **payload,
                "keyword": str(rule.get("pattern") or ""),
                "keyword_id": rule_id,
            }
            keyword_event = event_from_payload(keyword_payload)
            if keyword_event is None:
                continue
            await emit(
                keyword_event,
                payload=keyword_payload,
                targets=targets,
                rules=rules,
                is_open=is_open,
                push_already=push_already,
            )
    except Exception:
        logger.debug("keyword notification failed", exc_info=True)


def _radio_name() -> str | None:
    """The connected radio's name (memory only), for the mention matcher."""
    from app.services.radio_runtime import radio_runtime

    mc = radio_runtime.meshcore
    info = mc.self_info if mc is not None else None
    name = info.get("name") if isinstance(info, Mapping) else None
    return name if isinstance(name, str) else None


async def _mention_channels(
    rules: Mapping[str, Any],
    key: str,
    is_open: bool,
    muted: bool,
    blocked: bool,
) -> list[str]:
    """The channels a mention reaches (``rules.message_channel_ids``). The channels are read
    only here, for a message that is a mention of a private-key channel: not on the hot path."""
    state_key = f"channel-{key}"
    ids = rules_mod.candidate_channel_ids(rules, rules_mod.MENTION_EVENT, state_key)
    if not ids:
        return []
    cloud_ids = set() if is_open else await rules_mod.cloud_channel_ids(ids)
    return rules_mod.message_channel_ids(
        rules,
        state_key=state_key,
        is_dm=False,
        is_public_or_hashtag=is_open,
        muted=muted,
        blocked=blocked,
        mention=True,
        cloud_ids=cloud_ids,
    )


async def emit_payload(payload: Mapping[str, Any]) -> None:
    """Any other event, from the producers' payload dict (first seen, channel found,
    telemetry alert, radio watch, update)."""
    event = event_from_payload(payload)
    if event is None:
        logger.debug("no catalogue event for %r", payload.get("event"))
        return
    await emit(event, payload=dict(payload))


async def emit_oss_update(current: str, latest: str) -> bool:
    """``app.update``. True when the event was queued for at least one enabled, unpaused
    channel (the caller then records ``last_notified_update_version``); False when the event
    is off, its channels are disabled or the global pause holds it: it is offered again at the
    next check."""
    payload = {"event": "oss_update", "current": current, "latest": latest}
    event = event_from_payload(payload)
    if event is None:
        return False
    return bool(await emit(event, payload=payload))
