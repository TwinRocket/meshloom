"""Web Push dispatch manager.

Conversation enablement uses ``policy.conversation_is_enabled`` plus a
separate muted-channel circuit breaker. First-seen contact alerts are
sent out-of-band to subscriptions and never go through WebSocket.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pywebpush import WebPushException

from app.channel_constants import is_public_channel_key
from app.net.egress_guard import EgressBlocked
from app.notifications.render import (  # noqa: F401  (re-exported: callers import them from here)
    DESTINATION_CHANGED_EVENT,
    build_push_payload,
    escape_markdown,
    event_notification_text,
    format_duration,
    message_text_is_public,
    neutralise_mentions,
    state_key_for_message,
)
from app.push.policy import conversation_is_enabled
from app.push.send import send_push
from app.push.vapid import get_vapid_claims, get_vapid_private_key
from app.repository.channels import ChannelRepository
from app.repository.push_subscriptions import PushSubscriptionRepository
from app.repository.settings import (
    NOTIFICATION_MEDIA_KEYS,
    AppSettingsRepository,
    any_media_enabled,
)

logger = logging.getLogger(__name__)

# Name kept for the tests and callers that import it from here.
_build_payload = build_push_payload

_SEND_TIMEOUT = 15  # seconds per push send
# A forbidden address in a DNS answer deletes a subscription only when a send
# at least this long after the first one still meets it (a resolver can be
# wrong once).
FORBIDDEN_ANSWER_CONFIRM_SECONDS = 3600
# Bound of the in-memory refusal bookkeeping (least recently touched dropped).
_REFUSAL_MEMORY_MAX = 1024
_monotonic = time.monotonic  # tests replace it


def _subscription_info(sub: dict) -> dict:
    """Build the subscription_info dict that pywebpush expects."""
    return {
        "endpoint": sub["endpoint"],
        "keys": {
            "p256dh": sub["p256dh"],
            "auth": sub["auth"],
        },
    }


@dataclass
class _SendResult:
    sub_id: str
    success: bool = False
    expired: bool = False
    # Refused by the egress guard: a configuration state, not a delivery failure.
    refused: bool = False
    # Refused for good (``EgressBlocked.permanent``): no setting can lift it.
    invalid: bool = False


@dataclass(frozen=True)
class PushOutcome:
    """What one dispatch did: subscriptions tried and how many took the push."""

    total: int = 0
    delivered: int = 0


class PushManager:
    def __init__(self) -> None:
        # (subscription id, code) already logged at WARNING: a liftable refusal
        # repeats on every dispatch until the setting or DNS changes.
        self._refusals_logged: OrderedDict[tuple[str, str], None] = OrderedDict()
        # Subscription id -> monotonic time a DNS answer first held a forbidden address.
        self._forbidden_since: OrderedDict[str, float] = OrderedDict()

    def _forget(self, sub_id: str) -> None:
        for key in [k for k in self._refusals_logged if k[0] == sub_id]:
            del self._refusals_logged[key]
        self._forbidden_since.pop(sub_id, None)

    def _first_refusal(self, sub_id: str, code: str) -> bool:
        """True the first time (sub_id, code) is seen; the memory stays bounded."""
        key = (sub_id, code)
        if key in self._refusals_logged:
            self._refusals_logged.move_to_end(key)
            return False
        self._refusals_logged[key] = None
        while len(self._refusals_logged) > _REFUSAL_MEMORY_MAX:
            self._refusals_logged.popitem(last=False)
        return True

    def _forbidden_answer_confirmed(self, sub_id: str) -> bool:
        """Record a forbidden DNS answer; True once one is seen again past the delay."""
        now = _monotonic()
        first = self._forbidden_since.get(sub_id)
        if first is None:
            self._forbidden_since[sub_id] = now
            while len(self._forbidden_since) > _REFUSAL_MEMORY_MAX:
                self._forbidden_since.popitem(last=False)
            return False
        self._forbidden_since.move_to_end(sub_id)
        return now - first >= FORBIDDEN_ANSWER_CONFIRM_SECONDS

    async def dispatch_message(self, data: dict) -> None:
        """Send push notifications for a message event to all devices."""
        # Don't notify for messages the operator just sent themselves
        if data.get("outgoing"):
            return

        state_key = state_key_for_message(data)
        msg_type = data.get("type", "")
        conversation_key = str(data.get("conversation_key") or "")

        try:
            defaults = await AppSettingsRepository.get_push_defaults()
            overrides = await AppSettingsRepository.get_push_conversation_overrides()
        except Exception:
            logger.debug("Push dispatch: failed to load push preferences", exc_info=True)
            return

        is_hashtag = False
        is_public = False
        channel = None
        if msg_type == "CHAN" and conversation_key:
            is_public = is_public_channel_key(conversation_key)
            try:
                channel = await ChannelRepository.get_by_key(conversation_key)
            except Exception:
                logger.debug("Push dispatch: failed to load channel", exc_info=True)
            if channel is not None:
                is_hashtag = bool(channel.is_hashtag)
            if channel is not None and channel.membership == "pending":
                return

        flags = {
            media: conversation_is_enabled(
                state_key=state_key,
                message_type=msg_type,
                defaults=defaults,
                overrides=overrides,
                is_hashtag=is_hashtag,
                is_public=is_public,
                channel=media,
            )
            for media in NOTIFICATION_MEDIA_KEYS
        }
        if not any(flags.values()):
            return

        # Muted-channel circuit breaker — separate from conversation policy.
        if msg_type == "CHAN" and channel is not None and channel.muted:
            return

        if flags["apprise"]:
            from app.services.message_filters import is_blocked

            try:
                if is_blocked(data, await AppSettingsRepository.get()):
                    flags["apprise"] = False
            except Exception:
                logger.debug("Push dispatch: failed to load block lists", exc_info=True)
                flags["apprise"] = False

        from app.notify import dispatch_system_event

        payload = dict(data)
        payload.setdefault("event", "message")
        await dispatch_system_event(payload, flags)

    async def dispatch_event(self, data: dict) -> PushOutcome:
        """Send a push payload to all subscriptions. Not a WebSocket event."""
        return await self._send_to_all_subscriptions(
            lambda sub: build_push_payload(data, sub.get("language") or "fr")
        )

    async def dispatch_first_seen(self, contact: Mapping[str, Any]) -> None:
        """Send a first-seen contact alert to all subscriptions (no WebSocket)."""
        public_key = str(contact.get("public_key") or "")
        await self.dispatch_event(
            {
                "event": "first_seen",
                "public_key": public_key,
                "name": contact.get("name") or "",
                "contact_type": contact.get("type", 0),
            }
        )

    async def dispatch_oss_update(self, current: str, latest: str) -> bool:
        """Notify devices that a newer Meshloom release is available (no WebSocket).

        Returns True when the default is on and a send was attempted (even with
        zero subscriptions). Returns False when ``oss_update`` is disabled.
        """
        try:
            defaults = await AppSettingsRepository.get_push_defaults()
        except Exception:
            logger.debug("Push dispatch: failed to load push defaults", exc_info=True)
            return False
        if not any_media_enabled(defaults, "oss_update"):
            return False
        from app.notify import dispatch_system_event

        await dispatch_system_event(
            {
                "event": "oss_update",
                "current": current,
                "latest": latest,
            },
            defaults["oss_update"],
        )
        return True

    async def _send_to_all_subscriptions(
        self, payload_for_sub: Callable[[dict], str]
    ) -> PushOutcome:
        try:
            subs = await PushSubscriptionRepository.get_all()
        except Exception:
            logger.debug("Push dispatch: failed to load subscriptions", exc_info=True)
            return PushOutcome()

        if not subs:
            return PushOutcome()

        vapid_key = get_vapid_private_key()
        if not vapid_key:
            logger.debug("Push dispatch: no VAPID key configured, skipping")
            return PushOutcome()

        try:
            allow_private = (await AppSettingsRepository.get()).push_allow_private_endpoints
        except Exception:
            allow_private = False

        results = await asyncio.gather(
            *(self._send_one(sub, payload_for_sub(sub), vapid_key, allow_private) for sub in subs),
            return_exceptions=True,
        )
        await self._record_outcomes(results)
        delivered = sum(1 for r in results if isinstance(r, _SendResult) and r.success)
        return PushOutcome(total=len(subs), delivered=delivered)

    async def _record_outcomes(self, results: list[Any]) -> None:
        success_ids: list[str] = []
        failure_ids: list[str] = []
        remove_ids: list[str] = []
        for r in results:
            if isinstance(r, _SendResult):
                if r.invalid:
                    remove_ids.append(r.sub_id)
                    continue
                if r.refused:
                    continue  # kept, and never counted toward the eviction threshold
                if r.expired:
                    remove_ids.append(r.sub_id)
                elif r.success:
                    success_ids.append(r.sub_id)
                else:
                    failure_ids.append(r.sub_id)
        if success_ids or failure_ids or remove_ids:
            try:
                await PushSubscriptionRepository.batch_record_outcomes(
                    success_ids, failure_ids, remove_ids
                )
            except Exception:
                logger.debug("Push dispatch: failed to record outcomes", exc_info=True)

    async def _send_one(
        self, sub: dict, payload: str, vapid_key: str, allow_private: bool = False
    ) -> _SendResult:
        sub_id = sub["id"]
        result = _SendResult(sub_id=sub_id)
        try:
            async with asyncio.timeout(_SEND_TIMEOUT):
                await send_push(
                    subscription_info=_subscription_info(sub),
                    payload=payload,
                    vapid_private_key=vapid_key,
                    vapid_claims=get_vapid_claims(),
                    allow_private_network=allow_private,
                )
            result.success = True
            self._forget(sub_id)
        except EgressBlocked as e:
            result.refused = True
            if not (e.always_refused and not e.permanent):
                self._forbidden_since.pop(sub_id, None)  # the forbidden-answer streak broke
            if e.permanent or (e.always_refused and self._forbidden_answer_confirmed(sub_id)):
                # A bad URL, or an address forbidden whatever the setting (from a
                # DNS answer: met again an hour or more later): deleted.
                result.invalid = True
                self._forget(sub_id)
                logger.warning(
                    "Push endpoint refused for good by the egress guard (%s), removing %s",
                    e.code,
                    sub_id,
                )
            elif self._first_refusal(sub_id, e.code):
                # Kept and not counted as a failure: the endpoint becomes valid
                # again when the setting is ticked or DNS changes. Logged once.
                logger.warning("Push send refused by the egress guard for %s: %s", sub_id, e.code)
        except WebPushException as e:
            self._forbidden_since.pop(sub_id, None)
            status = getattr(e, "response", None)
            status_code = getattr(status, "status_code", 0) if status else 0
            if status_code in (403, 404, 410):
                logger.info("Push subscription expired (HTTP %d), removing %s", status_code, sub_id)
                result.expired = True
                self._forget(sub_id)
            else:
                # The type only: a WebPushException carries the push service's body.
                logger.warning("Push send failed for %s: %s", sub_id, type(e).__name__)
        except TimeoutError:
            self._forbidden_since.pop(sub_id, None)
            logger.warning("Push send timed out for %s", sub_id)
        except Exception as e:
            self._forbidden_since.pop(sub_id, None)
            # The type only: a requests error names the endpoint URL (its path is a secret).
            logger.warning("Push send error for %s: %s", sub_id, type(e).__name__)
        return result


push_manager = PushManager()
