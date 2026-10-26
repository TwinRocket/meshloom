"""Web Push as a transport: every deliverable subscription, each in its own language.

Wraps ``PushManager.dispatch_event`` (``_send_to_all_subscriptions``), which keeps
its per-subscription work: the payload is built in the subscription's language,
a push service answering 403, 404 or 410 deletes the subscription, the egress
guard's refusals and the failure counts stay as they are (``app/AGENTS.md``,
"Web Push"). A push is never retried by the dispatcher: a second attempt would
reach again the subscriptions that already took it.

A delivery with an ``audience`` (``app/notifications/user_rules.py``, MSHL-321) reaches only
the devices whose owner's rules want it, each with the payload those rules give it (for an
admin-only notice, the administrators' devices, MSHL-399); without one (a summary) every
deliverable device gets it, except an admin-only notice, which ``PushManager`` still keeps to
administrators.
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

        if event.audience is None:
            outcome = await push_manager.dispatch_event(dict(event.payload))
        else:  # each account's rules decide which of its devices get which payload
            outcome = await push_manager.dispatch_event(
                dict(event.payload), audience=event.audience
            )
        if isinstance(outcome, PushOutcome) and outcome.total and not outcome.delivered:
            return SendResult("failed", error_code="push_delivery_failed")
        return SEND_OK
