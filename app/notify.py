"""The 4.18.x notification media (push, email, webhook, Apprise) and their adapter.

The producers no longer call this module: they emit events and the rules choose the
channels (``app/notifications/producers.py``). The send functions below are what the
``legacy:*`` transports (``app/notifications/transports/legacy.py``) call until the
native ones replace them.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.models import NotificationDestinations
from app.net.apprise_urls import split_apprise_urls
from app.net.egress_guard import error_code, pinned_async_client, validate_destination
from app.notifications.apprise_medium import send_to_urls
from app.notifications.executor import run_blocking
from app.notifications.transports.email import build_email_apprise_url
from app.notifications.transports.email import send_email_sync as _send_email_sync

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 10.0

# The last webhook failure ({"code", "at"}), cleared by a successful send. In
# memory only, never the exception text. The health payload reads the channel
# status instead (``notification_statuses``, the dispatcher).
_webhook_last_error: dict[str, Any] | None = None
# The last Apprise failure ({"code", "at", "line", "url"}, the URL redacted),
# cleared by a send where every URL succeeded. Same rules.
_apprise_last_error: dict[str, Any] | None = None

TEST_ALERT_PAYLOAD = {
    "event": "telemetry_alert",
    "public_key": "aa" * 32,
    "name": "Test repeater",
    "rule_id": "battery",
    "metric": "battery",
    "unit": "V",
    "value": 3.46,
    "threshold": 3.5,
    "op": "lt",
}


async def _email_message(payload: Mapping[str, Any]) -> tuple[str, str]:
    from app.email_template import email_subject, render_html_email, resolve_instance_language

    lang = await resolve_instance_language()
    return email_subject(payload, lang), render_html_email(payload, lang)


async def send_email_alert(dest: NotificationDestinations, payload: Mapping[str, Any]) -> None:
    email = dest.email.model_dump(by_alias=True)
    if not email.get("host") and not email.get("to"):
        raise ValueError("email destination is not configured")
    url = build_email_apprise_url(email)
    title, body = await _email_message(payload)
    ok = await run_blocking(_send_email_sync, url, title, body)
    if not ok:
        raise RuntimeError("email send failed")


def webhook_last_error() -> dict[str, Any] | None:
    return dict(_webhook_last_error) if _webhook_last_error else None


def describe_webhook_failure(exc: BaseException, url: str) -> str:
    """Log text for a failed webhook send: code, type, host and HTTP status.

    Never the exception text nor the full URL: an ``HTTPStatusError`` carries
    the URL with its path, query and userinfo, where tokens live.
    """
    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        host = None
    return f"{error_code(exc)}: {type(exc).__name__} (host {host}, HTTP {status})"


async def send_webhook_alert(
    dest: NotificationDestinations, payload: Mapping[str, Any], *, dedup_key: str = ""
) -> None:
    """POST the payload through the egress guard, pinned to a validated address.

    The body and headers are the native webhook transport's
    (``app/notifications/transports/webhook.py``). Raises ``ValueError`` when no URL
    is set, ``EgressBlocked`` when the guard refuses it (before any connection),
    else the httpx error.
    """
    global _webhook_last_error
    url = (dest.webhook.url or "").strip()
    if not url:
        raise ValueError("webhook url is not configured")

    from app.notifications.transports.webhook import body_bytes, request_headers

    wire = body_bytes(payload)
    headers = request_headers(
        payload, wire, secret=dest.webhook.hmac_secret or "", dedup_key=dedup_key
    )

    try:
        target = await validate_destination(
            url, allow_private_network=dest.webhook.allow_private_network
        )
        async with pinned_async_client(
            target, timeout=httpx.Timeout(WEBHOOK_TIMEOUT_SECONDS)
        ) as client:
            resp = await client.post(content=wire, headers=headers)
            resp.raise_for_status()
    except Exception as exc:
        _webhook_last_error = {"code": error_code(exc), "at": int(time.time())}
        raise
    _webhook_last_error = None


def apprise_last_error() -> dict[str, Any] | None:
    return dict(_apprise_last_error) if _apprise_last_error else None


async def send_apprise_alert(dest: NotificationDestinations, payload: Mapping[str, Any]) -> None:
    """Send one event to every Apprise URL now, each URL on its own (no limiter).

    The text policy (``include_message_text``) and the format apply here, once.
    Raises ``ValueError`` when no URL is set, else the first URL's
    ``AppriseUrlRefused`` or ``AppriseSendFailed`` once every URL was tried.
    """
    global _apprise_last_error
    apprise = dest.apprise
    urls = split_apprise_urls(apprise.urls or "")
    if not urls:
        raise ValueError("apprise urls are not configured")

    from app.email_template import resolve_instance_language
    from app.push.manager import event_notification_text

    markdown = apprise.format == "markdown"
    title, body = event_notification_text(
        dict(payload),
        await resolve_instance_language(),
        include_message_text=apprise.include_message_text,
        markdown=markdown,
    )
    results = await send_to_urls(
        urls,
        allow_private_network=apprise.allow_private_network,
        title=title,
        body=body,
        markdown=markdown,
    )
    failures = [(result, result.error) for result in results if result.error is not None]
    for result, error in failures:
        logger.warning(
            "notify: Apprise URL %d (%s) failed: %s", result.line, result.url, error.code
        )
    if not failures:
        _apprise_last_error = None
        return
    last, last_error = failures[-1]
    _apprise_last_error = {
        "code": last_error.code,
        "at": int(time.time()),
        "line": last.line,
        "url": last.url,
    }
    raise failures[0][1]


# Fields that say nothing about where notifications go (a one-time banner).
_NOT_A_DESTINATION = {"webhook": {"private_network_grandfathered"}}


def destination_kinds_changed(
    before: NotificationDestinations, after: NotificationDestinations
) -> list[str]:
    """The destination kinds (``email``, ``webhook``, ``apprise``) whose settings changed."""
    old, new = before.model_dump(by_alias=True), after.model_dump(by_alias=True)
    changed = []
    for kind in new:
        ignored = _NOT_A_DESTINATION.get(kind, set())
        old_kind = {k: v for k, v in (old.get(kind) or {}).items() if k not in ignored}
        new_kind = {k: v for k, v in new[kind].items() if k not in ignored}
        if old_kind != new_kind:
            changed.append(kind)
    return changed


def notify_destination_changed(
    before: NotificationDestinations, after: NotificationDestinations
) -> None:
    """Proof of modification: a change of any destination pushes a notification
    to every subscription. Not configurable; never raises."""
    kinds = destination_kinds_changed(before, after)
    if not kinds:
        return
    try:
        from app.background_tasks import spawn
        from app.push.manager import DESTINATION_CHANGED_EVENT, push_manager

        spawn(
            push_manager.dispatch_event({"event": DESTINATION_CHANGED_EVENT, "kinds": kinds}),
            name="notify-destination-changed",
        )
    except Exception:
        logger.debug("notify: destination change notice failed", exc_info=True)
