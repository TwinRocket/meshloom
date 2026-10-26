"""The transport contract the dispatcher drives.

A transport sends one delivery to one channel and answers a ``SendResult``; it
never retries, sleeps or limits (the dispatcher's worker does) and never raises
for a failed send. ``status`` is:

- ``ok``: delivered;
- ``retry``: worth another attempt (a 5xx, a timeout, a network error, a 429
  with its ``retry_after`` in seconds when the service sent one);
- ``failed``: not worth one (any other 4xx, a refused destination, a
  destination that is not configured, a multi-target send that may have
  reached some targets).

``error_code`` is a stable code (``service_error``, ``timeout``, ``egress_*``,
``http_401``...), never an exception text: it reaches the health payload and
the deliveries journal after ``secret_fields.scrub_error``. Timeouts belong to
the transport (httpx timeout, Apprise ``cto``/``rto``, the push send's connect
and read timeouts); the dispatcher never wraps a thread in ``asyncio.timeout``.
Blocking work runs in ``app.notifications.executor.run_blocking``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from app.net.egress_guard import EgressBlocked, error_code
from app.notifications.channels import Channel
from app.notifications.events import NotificationEvent
from app.notifications.render import Rendered
from app.notifications.user_rules import PushAudience

# "skipped" only ends a test send (``NotificationDispatcher.send_test``): nothing was sent
# because nobody could be reached (a Web Push test with no audience or no device). It is
# neither a success nor a failure, so it never resumes a paused channel nor counts.
SendStatus = Literal["ok", "retry", "failed", "skipped"]
# The ``skipped`` code of a Web Push test that reached no device.
NO_PUSH_DEVICE = "no_push_device"


@dataclass(frozen=True)
class SendResult:
    status: SendStatus
    retry_after: float | None = None
    error_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


SEND_OK = SendResult("ok")


@dataclass(frozen=True)
class Delivery:
    """What a worker hands a transport: the catalogue event when the payload maps to
    one, and the legacy payload dict the existing renderers take."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    event: NotificationEvent | None = None
    # Web Push only: which payload each account's devices get (``user_rules.PushAudience``).
    audience: PushAudience | None = None


class Transport(Protocol):
    async def send(
        self,
        channel: Channel,
        rendered: Rendered | None,
        event: Delivery,
        secrets: dict[str, str],
    ) -> SendResult:
        """Send ``event`` to ``channel``. ``rendered`` is None for a transport that
        renders per recipient (push: each subscription's language) or through the
        legacy helpers; ``secrets`` are the channel's write-only values."""
        ...


def parse_retry_after(value: str | None) -> float | None:
    """A ``Retry-After`` header in seconds (delta or HTTP date), None when absent."""
    from app.notifications.apprise_medium import _parse_retry_after

    return _parse_retry_after(value)


def result_from_exception(exc: BaseException) -> SendResult:
    """The policy for an httpx send: retry 5xx, 429, timeouts and network errors only."""
    if isinstance(exc, EgressBlocked):
        return SendResult("failed", error_code=exc.code)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return SendResult(
                "retry",
                retry_after=parse_retry_after(exc.response.headers.get("Retry-After")),
                error_code="rate_limited",
            )
        if status >= 500:
            return SendResult("retry", error_code="service_error")
        return SendResult("failed", error_code=f"http_{status}")
    if isinstance(exc, httpx.TransportError | TimeoutError | OSError):
        code = error_code(exc)
        # A certificate the destination presents now, it presents again in 5 s.
        return SendResult("failed" if code == "tls_error" else "retry", error_code=code)
    return SendResult("failed", error_code="send_failed")


async def channel_language(channel: Channel) -> Literal["fr", "en"]:
    """The language a channel's text is rendered in: its own, else the instance's."""
    if channel.language in ("fr", "en"):
        return channel.language
    from app.email_template import resolve_instance_language

    return await resolve_instance_language()
