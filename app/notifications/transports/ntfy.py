"""The ntfy transport: ntfy's JSON publish mode, to a server pinned to a validated IP.

``POST {server}`` with ``{topic, title, message, priority, tags, click, markdown}``.
JSON rather than ``Title:`` headers because httpx encodes headers in ASCII (a French
title raises ``UnicodeEncodeError``), and because the topic, the only secret on ntfy.sh,
then stays out of the URL and of every log line. The topic, the token and the password
are write-only channel secrets, never logged.

Answers: 401/403 ``auth_failed``; 413 ``service_error``; 429 ``rate_limited`` with the
server's ``Retry-After`` (the dispatcher waits that long), or ``quota_exceeded`` when
the body names a daily/message quota (not retried: it does not clear in seconds); 5xx
``service_error`` (retried); a certificate failure ``tls_error``. Retries are the
dispatcher's.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import httpx

from app.net.egress_guard import (
    pinned_async_client,
    tls_check_may_be_off,
    validate_destination,
)
from app.notifications.channels import NTFY_DEFAULT_SERVER, Channel
from app.notifications.render import Rendered, build_push_payload, event_notification_text
from app.notifications.transports.base import (
    SEND_OK,
    Delivery,
    SendResult,
    channel_language,
    parse_retry_after,
    result_from_exception,
)

logger = logging.getLogger(__name__)

NTFY_TIMEOUT_SECONDS = 10.0
# ntfy's own wording of a spent allowance ("daily message quota reached", "limit reached:
# ... daily ... ") also matches its numeric 4290x family; the text is what we can test.
_QUOTA_WORDS = ("quota", "daily", "message limit")


def auth_header(secrets: dict[str, str], config: dict[str, Any]) -> dict[str, str]:
    """``Authorization`` for a token (Bearer) or a user name and password (Basic)."""
    token = (secrets.get("token") or "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    username = str(config.get("username") or "")
    password = secrets.get("password") or ""
    if username and password:
        raw = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        return {"Authorization": f"Basic {raw}"}
    return {}


def is_quota_response(response: httpx.Response) -> bool:
    """A 429 whose body says the daily allowance is spent (ntfy.sh's free plan)."""
    try:
        error = str(response.json().get("error") or "")
    except Exception:
        error = response.text[:500]
    lowered = error.lower()
    return any(word in lowered for word in _QUOTA_WORDS)


def build_body(
    channel: Channel, secrets: dict[str, str], title: str, message: str, event: Delivery, lang: str
) -> dict[str, Any]:
    config = channel.config
    urgent = bool(event.event is not None and event.event.urgent)
    priority = config.get("priority_urgent", 5) if urgent else config.get("priority_default", 3)
    body: dict[str, Any] = {
        "topic": secrets["topic"].strip(),
        "title": title,
        "message": message,
        "priority": int(priority),
    }
    tags = [t.strip() for t in str(config.get("tags") or "").split(",") if t.strip()]
    if tags:
        body["tags"] = tags
    base = str(config.get("click_url") or "").strip()
    if base:
        link = str(json.loads(build_push_payload(dict(event.payload), lang)).get("url_hash") or "")
        body["click"] = base + link
    if config.get("markdown"):
        body["markdown"] = True
    return body


class NtfyTransport:
    async def send(
        self,
        channel: Channel,
        rendered: Rendered | None,
        event: Delivery,
        secrets: dict[str, str],
    ) -> SendResult:
        if not (secrets.get("topic") or "").strip():
            return SendResult("failed", error_code="not_configured")
        lang = await channel_language(channel)
        markdown = bool(channel.config.get("markdown"))
        title, message = event_notification_text(
            dict(event.payload),
            lang,
            include_message_text=channel.include_message_text,
            markdown=markdown,
        )
        body = build_body(channel, secrets, title, message, event, lang)
        headers = {"Content-Type": "application/json", **auth_header(secrets, channel.config)}
        server = str(channel.config.get("server") or NTFY_DEFAULT_SERVER)
        try:
            target = await validate_destination(
                server, allow_private_network=channel.allow_private_network
            )
            if not channel.verify_tls and not tls_check_may_be_off(target.ips):
                return SendResult("failed", error_code="verify_tls_requires_private_destination")
            async with pinned_async_client(
                target,
                verify_tls=channel.verify_tls,
                timeout=httpx.Timeout(NTFY_TIMEOUT_SECONDS),
            ) as client:
                response = await client.post(
                    content=json.dumps(body, ensure_ascii=False).encode(), headers=headers
                )
            result = _result(response)
            if result.status != "ok":
                logger.warning("notification ntfy %s: %s", channel.id, result.error_code)
            return result
        except Exception as exc:
            result = result_from_exception(exc)
            # The type and the code: an httpx error text carries the URL.
            logger.warning(
                "notification ntfy %s: %s (%s)", channel.id, result.error_code, type(exc).__name__
            )
            return result


def _result(response: httpx.Response) -> SendResult:
    status = response.status_code
    if status < 400:
        return SEND_OK
    if status in (401, 403):
        return SendResult("failed", error_code="auth_failed")
    if status == 413:
        return SendResult("failed", error_code="service_error")
    if status == 429:
        if is_quota_response(response):
            return SendResult("failed", error_code="quota_exceeded")
        return SendResult(
            "retry",
            retry_after=parse_retry_after(response.headers.get("Retry-After")),
            error_code="rate_limited",
        )
    if status >= 500:
        return SendResult("retry", error_code="service_error")
    return SendResult("failed", error_code=f"http_{status}")
