"""Web Push as a transport: every deliverable subscription, each in its own language.

Wraps ``PushManager.dispatch_event`` (``_send_to_all_subscriptions``), which keeps
its per-subscription work: the payload is built in the subscription's language,
a push service answering 403, 404 or 410 deletes the subscription, the egress
guard's refusals and the failure counts stay as they are (``app/AGENTS.md``,
"Web Push"). A push is never retried by the dispatcher: a second attempt would
reach again the subscriptions that already took it.

A delivery with an ``audience`` (``app/notifications/user_rules.py``, MSHL-321) reaches only
the devices whose owner's rules want it, each with the payload those rules give it (for an
admin-only notice, a keyword alert or a watched node, the administrators' devices, MSHL-399 /
MSHL-400); without one (a summary) every deliverable device gets it, except those events, which
``PushManager`` still keeps to administrators. A message's text only reaches a device whose
owner may read it in the app; another gets a generic notice (MSHL-400).
"""

from __future__ import annotations

from app.notifications.channels import Channel
from app.notifications.render import Rendered
from app.notifications.transports.base import NO_PUSH_DEVICE, SEND_OK, Delivery, SendResult


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
        if event.name == "notify.test" and isinstance(outcome, PushOutcome) and not outcome.total:
            # A test that reached no device sent nothing: say so (MSHL-407).
            return SendResult("skipped", error_code=NO_PUSH_DEVICE)
        return SEND_OK
