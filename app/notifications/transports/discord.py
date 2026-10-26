"""The native Discord transport: one JSON POST to a webhook, pinned, no mention ever parsed.

A channel message relayed by a webhook pings the whole server when it contains
``@everyone`` unless the request carries ``allowed_mentions: {"parse": []}``; Apprise
never sends it, hence this transport. The body is ``content`` (the event's title and
text, mentions neutralised and Markdown escaped by ``app.notifications.markdown``),
``username`` when the channel sets one (never an avatar), ``allowed_mentions`` and
``flags: 4`` (no link preview card: the card would make Discord fetch a mesh-supplied URL).

The webhook URL is a write-only secret (its path carries the token), limited to
``discord.com`` / ``discordapp.com`` over https, resolved and pinned by the egress guard.
``verify_tls=false`` is refused: the host is public. Rate limits: a 429 answers ``retry``
with Discord's ``retry_after``; the ``X-RateLimit-Remaining: 0`` / ``Reset-After`` headers
of an answer make the next send to the same webhook wait (``retry`` with the remaining
delay, nothing sent) instead of earning a 429. The transport never sleeps itself.
Codes: 401/403/404 ``auth_failed`` (deleted or wrong webhook), 400 ``invalid_config``,
5xx ``service_error`` (retried), else ``http_NNN``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

import httpx

from app.net.egress_guard import (
    HostAllowlist,
    pinned_async_client,
    tls_check_may_be_off,
    validate_destination,
)
from app.notifications.channels import DISCORD_HOSTS, Channel, parse_discord_webhook
from app.notifications.render import Rendered, event_notification_text
from app.notifications.transports.base import (
    SEND_OK,
    Delivery,
    SendResult,
    channel_language,
    parse_retry_after,
    result_from_exception,
)

logger = logging.getLogger(__name__)

DISCORD_TIMEOUT_SECONDS = 10.0
CONTENT_LIMIT = 2000
SUPPRESS_EMBEDS = 1 << 2
_ALLOWED_HOSTS = HostAllowlist.parse(DISCORD_HOSTS)
# A webhook's bucket, keyed by a hash of its id: when Discord said it is empty, until when.
_blocked_until: dict[str, float] = {}
_MAX_TRACKED = 256
_TOKEN_PATH = re.compile(r"(/webhooks/\d+/)[^/?#\s]+")


class _WebhookTokenRedaction(logging.Filter):
    """httpx logs every request URL at INFO, and a Discord token sits in the path
    (the egress guard's filter only drops the query and user info)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                arg.copy_with(path=_TOKEN_PATH.sub(r"\1***", arg.path))
                if isinstance(arg, httpx.URL) and "/webhooks/" in arg.path
                else arg
                for arg in record.args
            )
        return True


# Once per process, at import: this module is the only sender of Discord URLs.
logging.getLogger("httpx").addFilter(_WebhookTokenRedaction())


def build_content(title: str, body: str) -> str:
    """``**title**`` and the text, both already escaped, cut to Discord's 2000 characters."""
    text = f"**{title}**\n{body}" if body and body != title else f"**{title}**"
    if len(text) <= CONTENT_LIMIT:
        return text
    return text[: CONTENT_LIMIT - 1].rstrip("\\") + "…"


def build_body(channel: Channel, title: str, body: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "content": build_content(title, body),
        "allowed_mentions": {"parse": []},
        "flags": SUPPRESS_EMBEDS,
    }
    username = str(channel.config.get("username") or "").strip()
    if username:
        payload["username"] = username
    return payload


def _float(value: str | None) -> float | None:
    try:
        number = float(value) if value is not None else None
    except ValueError:
        return None
    return number if number is not None and number >= 0 else None


def _retry_after(response: httpx.Response) -> float | None:
    """The longest of the ``Retry-After`` header, ``X-RateLimit-Reset-After`` and the JSON
    body's ``retry_after`` (seconds, possibly fractional)."""
    candidates = [parse_retry_after(response.headers.get("Retry-After"))]
    candidates.append(_float(response.headers.get("X-RateLimit-Reset-After")))
    try:
        data = response.json()
        if isinstance(data, dict) and isinstance(data.get("retry_after"), int | float):
            candidates.append(float(data["retry_after"]))
    except ValueError:
        pass
    known = [value for value in candidates if value is not None and value >= 0]
    return max(known) if known else None


def _note_bucket(key: str, response: httpx.Response) -> None:
    if response.headers.get("X-RateLimit-Remaining") == "0":
        reset = _float(response.headers.get("X-RateLimit-Reset-After"))
        if reset:
            if len(_blocked_until) >= _MAX_TRACKED:
                _forget_expired()
            _blocked_until[key] = time.monotonic() + reset
            return
    _blocked_until.pop(key, None)


def _forget_expired() -> None:
    now = time.monotonic()
    for key in [k for k, until in _blocked_until.items() if until <= now]:
        del _blocked_until[key]


def _rate_limit_wait(key: str) -> float:
    return max(0.0, _blocked_until.get(key, 0.0) - time.monotonic())


def result_from_response(response: httpx.Response) -> SendResult:
    status = response.status_code
    if status < 300:
        return SEND_OK
    if status == 429:
        return SendResult("retry", retry_after=_retry_after(response), error_code="rate_limited")
    if status in (401, 403, 404):
        return SendResult("failed", error_code="auth_failed")
    if status == 400:
        return SendResult("failed", error_code="invalid_config")
    if status >= 500:
        return SendResult("retry", error_code="service_error")
    return SendResult("failed", error_code=f"http_{status}")


class DiscordTransport:
    async def send(
        self,
        channel: Channel,
        rendered: Rendered | None,
        event: Delivery,
        secrets: dict[str, str],
    ) -> SendResult:
        url = (secrets.get("webhook_url") or "").strip()
        if not url:
            return SendResult("failed", error_code="not_configured")
        parsed = parse_discord_webhook(url)
        if parsed is None:
            return SendResult("failed", error_code="invalid_config")
        bucket = hashlib.sha256(parsed[0].encode()).hexdigest()[:16]
        wait = _rate_limit_wait(bucket)
        if wait > 0:
            return SendResult("retry", retry_after=wait, error_code="rate_limited")
        title, body = event_notification_text(
            dict(event.payload),
            await channel_language(channel),
            include_message_text=channel.include_message_text,
            markdown=True,
            template_dm=channel.template_dm,
            template_channel=channel.template_channel,
        )
        data = json.dumps(build_body(channel, title, body), separators=(",", ":")).encode()
        try:
            target = await validate_destination(
                url,
                allow_private_network=channel.allow_private_network,
                schemes=frozenset({"https"}),
                allowed_hosts=_ALLOWED_HOSTS,
            )
            if not channel.verify_tls and not tls_check_may_be_off(target.ips):
                return SendResult("failed", error_code="verify_tls_requires_private_destination")
            async with pinned_async_client(
                target,
                verify_tls=channel.verify_tls,
                timeout=httpx.Timeout(DISCORD_TIMEOUT_SECONDS),
            ) as client:
                response = await client.post(
                    content=data, headers={"Content-Type": "application/json"}
                )
        except Exception as exc:
            result = result_from_exception(exc)
            # The type and the code: an httpx error text carries the URL and its token.
            logger.warning(
                "notification discord %s: %s (%s)",
                channel.id,
                result.error_code,
                type(exc).__name__,
            )
            return result
        _note_bucket(bucket, response)
        result = result_from_response(response)
        if not result.ok:
            logger.warning(
                "notification discord %s: %s (HTTP %d)",
                channel.id,
                result.error_code,
                response.status_code,
            )
        return result
