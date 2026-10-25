"""Web Push as a transport: every deliverable subscription, each in its own language.

Wraps ``PushManager.dispatch_event`` (``_send_to_all_subscriptions``), which keeps
its per-subscription work: the payload is built in the subscription's language,
a push service answering 403, 404 or 410 deletes the subscription, the egress
guard's refusals and the failure counts stay as they are (``app/AGENTS.md``,
"Web Push"). A push is never retried by the dispatcher: a second attempt would
reach again the subscriptions that already took it.
"""

from __future__ import annotations

from app.notifications.channels import Channel
from app.notifications.render import Rendered
from app.notifications.transports.base import SEND_OK, Delivery, SendResult


class WebPushTransport:
    async def send(
        self,
        channel: Channel,
        rendered: Rendered | None,
        event: Delivery,
        secrets: dict[str, str],
    ) -> SendResult:
        from app.push.manager import PushOutcome, push_manager

        outcome = await push_manager.dispatch_event(dict(event.payload))
        if isinstance(outcome, PushOutcome) and outcome.total and not outcome.delivered:
            return SendResult("failed", error_code="push_delivery_failed")
        return SEND_OK
