"""The webhook transport: the v1 JSON body, signed, to a URL pinned to a validated IP.

The body and ``X-Webhook-Signature`` are the 4.18.x ones, byte for byte: compact JSON
with sorted keys, ``sha256=`` + HMAC-SHA256 of those bytes under the channel's
secret. M1 adds two headers and leaves the signature alone (it does not cover
them): ``X-Webhook-Id`` (the event's ``dedup_key``, the same on every retry, so a
receiver such as Node-RED deduplicates a slow 200 that was delivered twice) and
``X-Webhook-Timestamp`` (epoch seconds of this attempt, to reject an old replay).
A signature that covers the timestamp is the v2 body (MSHL-140).

The URL and the secret are write-only channel secrets, never logged. Retries are the
dispatcher's: this transport answers ``retry`` for a 5xx, a 429, a timeout or a
network error, ``failed`` for anything else.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Mapping
from typing import Any

import httpx

from app.net.egress_guard import pinned_async_client, validate_destination
from app.notifications.channels import Channel
from app.notifications.render import Rendered, enrich_notification_payload
from app.notifications.transports.base import (
    SEND_OK,
    Delivery,
    SendResult,
    result_from_exception,
)

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 10.0
SIGNATURE_PREFIX = "sha256="


def body_bytes(payload: Mapping[str, Any]) -> bytes:
    """The v1 body: the enriched payload as compact JSON, keys sorted."""
    return json.dumps(
        enrich_notification_payload(payload), separators=(",", ":"), sort_keys=True
    ).encode()


def sign(secret: str, body: bytes) -> str:
    """The ``X-Webhook-Signature`` value for ``body``."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time check of a received ``X-Webhook-Signature`` (a receiver's helper,
    and what the tests pin the format with)."""
    if not secret or not header:
        return False
    return hmac.compare_digest(sign(secret, body).encode(), header.encode())


def request_headers(
    payload: Mapping[str, Any],
    body: bytes,
    *,
    secret: str = "",
    dedup_key: str = "",
    now: float | None = None,
) -> dict[str, str]:
    """Headers of one webhook POST. ``dedup_key`` falls back to a hash of the body
    for a delivery that is not a catalogue event (a grouped summary)."""
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event": str(enrich_notification_payload(payload).get("event") or "notification"),
        "X-Webhook-Id": dedup_key or hashlib.sha256(body).hexdigest()[:16],
        "X-Webhook-Timestamp": str(int(time.time() if now is None else now)),
    }
    if secret:
        headers["X-Webhook-Signature"] = sign(secret, body)
    return headers


class WebhookTransport:
    async def send(
        self,
        channel: Channel,
        rendered: Rendered | None,
        event: Delivery,
        secrets: dict[str, str],
    ) -> SendResult:
        url = (secrets.get("url") or "").strip()
        if not url:
            return SendResult("failed", error_code="not_configured")
        if not channel.verify_tls and not channel.allow_private_network:
            return SendResult("failed", error_code="verify_tls_requires_private_network")
        body = body_bytes(event.payload)
        headers = request_headers(
            event.payload,
            body,
            secret=secrets.get("hmac_secret", ""),
            dedup_key=event.event.dedup_key if event.event is not None else "",
        )
        try:
            target = await validate_destination(
                url, allow_private_network=channel.allow_private_network
            )
            async with pinned_async_client(
                target,
                verify_tls=channel.verify_tls,
                timeout=httpx.Timeout(WEBHOOK_TIMEOUT_SECONDS),
            ) as client:
                response = await client.post(content=body, headers=headers)
                response.raise_for_status()
        except Exception as exc:
            result = result_from_exception(exc)
            # The type and the code: an httpx error text carries the URL and its token.
            logger.warning(
                "notification webhook %s: %s (%s)",
                channel.id,
                result.error_code,
                type(exc).__name__,
            )
            return result
        return SEND_OK
