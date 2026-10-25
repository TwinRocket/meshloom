"""Web Push transport: sends one payload to every deliverable subscription.

A subscription is deliverable while its owner is enabled and holds ``send``
(``PushSubscriptionRepository.deliverable``, MSHL-290).

Which events reach Web Push is decided by the notification rules and the dispatcher
(``app/notifications``); this manager only sends (``dispatch_event``) and keeps the
subscriptions healthy.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pywebpush import WebPushException

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
from app.push.send import send_push
from app.push.vapid import get_vapid_claims, get_vapid_private_key
from app.repository.push_subscriptions import PushSubscriptionRepository
from app.repository.settings import AppSettingsRepository

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

    async def dispatch_event(self, data: dict) -> PushOutcome:
        """Send a push payload to every deliverable subscription. Not a WebSocket event."""
        return await self._send_to_all_subscriptions(
            lambda sub: build_push_payload(data, sub.get("language") or "fr")
        )

    async def _send_to_all_subscriptions(
        self, payload_for_sub: Callable[[dict], str]
    ) -> PushOutcome:
        try:
            subs = await PushSubscriptionRepository.deliverable()
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
