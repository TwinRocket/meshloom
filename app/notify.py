"""System-event dispatch for telemetry alerts (push, email, webhook)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from app.models import NotificationDestinations, TelemetryAlertChannels
from app.net.egress_guard import error_code, pinned_async_client, validate_destination
from app.repository.settings import AppSettingsRepository

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 10.0

# The last webhook failure ({"code", "at"}), cleared by a successful send. In
# memory only, for the health payload (notifications T3); never the exception text.
_webhook_last_error: dict[str, Any] | None = None

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


def _channel_flags(channels: TelemetryAlertChannels | Mapping[str, Any]) -> TelemetryAlertChannels:
    if isinstance(channels, TelemetryAlertChannels):
        return channels
    return TelemetryAlertChannels(
        push=bool(channels.get("push", False)),
        email=bool(channels.get("email", False)),
        webhook=bool(channels.get("webhook", False)),
    )


async def _email_message(payload: Mapping[str, Any]) -> tuple[str, str]:
    from app.email_template import email_subject, render_html_email, resolve_instance_language

    lang = await resolve_instance_language()
    return email_subject(payload, lang), render_html_email(payload, lang)


def build_email_apprise_url(email: Mapping[str, Any]) -> str:
    """Build an ephemeral Apprise mailto/mailtos URL. Never persist this string."""
    mode = str(email.get("mode") or "starttls")
    if mode == "none":
        scheme = "mailto"
        extra: dict[str, str] = {}
    elif mode == "ssl":
        scheme = "mailtos"
        extra = {"mode": "ssl"}
    else:
        scheme = "mailtos"
        extra = {}

    params: dict[str, str] = {}
    user = str(email.get("user") or "")
    password = str(email.get("password") or "")
    host = str(email.get("host") or "")
    from_addr = str(email.get("from") or email.get("from_address") or "")
    to_addr = str(email.get("to") or "")
    port = email.get("port")
    if user:
        params["user"] = user
    if password:
        params["pass"] = password
    if host:
        params["smtp"] = host
    if from_addr:
        params["from"] = from_addr
    if to_addr:
        params["to"] = to_addr
    if port not in (None, ""):
        params["port"] = str(port)
    params.update(extra)
    # Embed image attachments referenced by cid: (the logo) as inline parts of
    # a multipart/related message, instead of downloadable attachments.
    params["inline"] = "yes"
    return f"{scheme}://_?{urlencode(params)}"


def _send_email_sync(url: str, title: str, body: str) -> bool:
    import apprise as apprise_lib
    from apprise import NotifyFormat
    from apprise.attachment.file import AttachFile

    from app.email_template import LOGO_CID, LOGO_PATH

    notifier = apprise_lib.Apprise()
    if not notifier.add(url):
        return False
    # The logo travels inside the email (cid:), so opening it fetches nothing.
    attach = apprise_lib.AppriseAttachment()
    attach.add(AttachFile(str(LOGO_PATH), name=LOGO_CID, mimetype="image/png"))
    return bool(
        notifier.notify(title=title, body=body, body_format=NotifyFormat.HTML, attach=attach)
    )


async def send_email_alert(dest: NotificationDestinations, payload: Mapping[str, Any]) -> None:
    email = dest.email.model_dump(by_alias=True)
    if not email.get("host") and not email.get("to"):
        raise ValueError("email destination is not configured")
    url = build_email_apprise_url(email)
    title, body = await _email_message(payload)
    ok = await asyncio.to_thread(_send_email_sync, url, title, body)
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


async def send_webhook_alert(dest: NotificationDestinations, payload: Mapping[str, Any]) -> None:
    """POST the payload through the egress guard, pinned to a validated address.

    Raises ``ValueError`` when no URL is set, ``EgressBlocked`` when the guard
    refuses it (before any connection), else the httpx error.
    """
    global _webhook_last_error
    url = (dest.webhook.url or "").strip()
    if not url:
        raise ValueError("webhook url is not configured")

    from app.email_template import enrich_notification_payload

    body = enrich_notification_payload(payload)
    body_bytes = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event": str(body.get("event") or "notification"),
    }
    secret = dest.webhook.hmac_secret or ""
    if secret:
        sig = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = f"sha256={sig}"

    try:
        target = await validate_destination(
            url, allow_private_network=dest.webhook.allow_private_network
        )
        async with pinned_async_client(
            target, timeout=httpx.Timeout(WEBHOOK_TIMEOUT_SECONDS)
        ) as client:
            resp = await client.post(content=body_bytes, headers=headers)
            resp.raise_for_status()
    except Exception as exc:
        _webhook_last_error = {"code": error_code(exc), "at": int(time.time())}
        raise
    _webhook_last_error = None


async def dispatch_system_event(
    payload: dict[str, Any],
    channels: TelemetryAlertChannels | Mapping[str, Any],
) -> None:
    """Fan a system event to the enabled transports. Never raises to callers."""
    flags = _channel_flags(channels)
    try:
        dest = (await AppSettingsRepository.get()).notification_destinations
    except Exception:
        dest = NotificationDestinations()
        logger.debug("notify: failed to load notification destinations", exc_info=True)

    if flags.push:
        try:
            from app.push.manager import push_manager

            await push_manager.dispatch_event(payload)
        except Exception:
            logger.debug("notify: push dispatch failed", exc_info=True)

    if flags.email:
        try:
            await send_email_alert(dest, payload)
        except Exception:
            logger.debug("notify: email dispatch failed", exc_info=True)

    if flags.webhook:
        try:
            await send_webhook_alert(dest, payload)
        except ValueError:
            logger.debug("notify: webhook not configured")
        except Exception as exc:
            logger.warning(
                "notify: webhook dispatch failed: %s",
                describe_webhook_failure(exc, dest.webhook.url or ""),
            )
