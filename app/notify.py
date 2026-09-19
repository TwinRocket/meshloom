"""System-event dispatch for telemetry alerts (push, email, webhook)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from app.models import NotificationDestinations, TelemetryAlertChannels
from app.repository.settings import AppSettingsRepository

logger = logging.getLogger(__name__)

TEST_ALERT_PAYLOAD = {
    "event": "telemetry_alert",
    "public_key": "",
    "name": "Meshloom",
    "rule_id": "test",
    "value": 0,
    "threshold": 0,
}


def _channel_flags(channels: TelemetryAlertChannels | Mapping[str, Any]) -> TelemetryAlertChannels:
    if isinstance(channels, TelemetryAlertChannels):
        return channels
    return TelemetryAlertChannels(
        push=bool(channels.get("push", False)),
        email=bool(channels.get("email", False)),
        webhook=bool(channels.get("webhook", False)),
    )


def _alert_text(payload: Mapping[str, Any]) -> tuple[str, str]:
    from app.push.manager import _telemetry_alert_titles

    return _telemetry_alert_titles(dict(payload), "en")


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
    return f"{scheme}://_?{urlencode(params)}"


def _send_email_sync(url: str, title: str, body: str) -> bool:
    import apprise as apprise_lib

    notifier = apprise_lib.Apprise()
    if not notifier.add(url):
        return False
    return bool(notifier.notify(title=title, body=body))


async def send_email_alert(dest: NotificationDestinations, payload: Mapping[str, Any]) -> None:
    email = dest.email.model_dump(by_alias=True)
    if not email.get("host") and not email.get("to"):
        raise ValueError("email destination is not configured")
    url = build_email_apprise_url(email)
    title, body = _alert_text(payload)
    ok = await asyncio.to_thread(_send_email_sync, url, title, body)
    if not ok:
        raise RuntimeError("email send failed")


async def send_webhook_alert(dest: NotificationDestinations, payload: Mapping[str, Any]) -> None:
    url = (dest.webhook.url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("webhook url must be http or https")

    body_bytes = json.dumps(dict(payload), separators=(",", ":"), sort_keys=True).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event": "telemetry_alert",
    }
    secret = dest.webhook.hmac_secret or ""
    if secret:
        sig = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = f"sha256={sig}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.post(url, content=body_bytes, headers=headers)
        resp.raise_for_status()


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
        except Exception:
            logger.debug("notify: webhook dispatch failed", exc_info=True)
