"""Fanout module for webhook (HTTP POST) delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

import httpx

from app.fanout.base import FanoutModule
from app.net.egress_guard import (
    EgressBlocked,
    error_code,
    pinned_async_client,
    validate_destination,
)

logger = logging.getLogger(__name__)


class WebhookModule(FanoutModule):
    """Delivers message data to an HTTP endpoint via POST (or configurable method)."""

    def __init__(self, config_id: str, config: dict, *, name: str = "") -> None:
        super().__init__(config_id, config, name=name)
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._last_error = None

    async def stop(self) -> None:
        self._running = False

    async def on_message(self, data: dict) -> None:
        await self._send(data, event_type="message")

    async def _send(self, data: dict, *, event_type: str) -> None:
        if not self._running:
            return

        url = self.config.get("url", "")
        if not url:
            return

        method = self.config.get("method", "POST").upper()
        extra_headers = self.config.get("headers", {})
        hmac_secret = self.config.get("hmac_secret", "")
        hmac_header = self.config.get("hmac_header", "X-Webhook-Signature")

        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Event": event_type,
            **extra_headers,
        }

        body_bytes = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()

        if hmac_secret:
            sig = hmac.new(hmac_secret.encode(), body_bytes, hashlib.sha256).hexdigest()
            headers[hmac_header or "X-Webhook-Signature"] = f"sha256={sig}"

        try:
            # Checked again before every send, then sent pinned to a validated
            # IP: the DNS answer cannot change between the check and the send.
            dest = await validate_destination(
                url, allow_private_network=bool(self.config.get("allow_private_network", False))
            )
            async with pinned_async_client(dest, timeout=httpx.Timeout(10.0)) as client:
                resp = await client.request(method, content=body_bytes, headers=headers)
            resp.raise_for_status()
            self._set_last_error(None)
        except EgressBlocked as exc:
            self._set_last_error(exc.code)
            logger.warning("Webhook %s destination refused (%s)", self.config_id, exc.code)
        except httpx.HTTPStatusError as exc:
            self._set_last_error(f"HTTP {exc.response.status_code}")
            # The URL may carry a token: log the host only.
            logger.warning(
                "Webhook %s returned %s from %s",
                self.config_id,
                exc.response.status_code,
                httpx.URL(url).host,
            )
        except httpx.RequestError as exc:
            self._set_last_error(error_code(exc))
            logger.warning(
                "Webhook %s request error (%s): %s",
                self.config_id,
                error_code(exc),
                type(exc).__name__,
            )

    @property
    def status(self) -> str:
        if not self.config.get("url"):
            return "disconnected"
        if self.last_error:
            return "error"
        return "connected"
