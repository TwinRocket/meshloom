"""The generic Apprise transport: one Apprise object per URL, each its own result.

The escape hatch for every service without a native transport or a preset. The
channel's ``urls`` (one per line) are write-only secrets. A preset channel (``presets.py``)
has no ``urls``: its URL is compiled from its fields right before the send, then goes
through the same path. Each URL is checked by the
egress guard right before its send (scheme allowlist, user-chosen host, redirects
off), sent on its own with Apprise's connect and read timeouts, and retried by
``apprise_medium`` (0/5/30 s on a network error, a 5xx or a 429) while the others are
unaffected. A URL that still fails after the others took the alert makes the delivery
``failed``, never ``retry``: the dispatcher's retry would resend to the URLs that
succeeded. The error code names the line and the failure code only: the redacted URL stays
in the log, since its host is part of a write-only secret. The text and the Markdown escaping come from the renderer, in the
channel's language and under its text policy.
"""

from __future__ import annotations

import logging

from app.net.apprise_urls import split_apprise_urls
from app.notifications.apprise_medium import send_to_urls
from app.notifications.channels import Channel
from app.notifications.presets import PresetError, compile_url, preset_for
from app.notifications.render import Rendered, event_notification_text
from app.notifications.transports.base import SEND_OK, Delivery, SendResult, channel_language

logger = logging.getLogger(__name__)


class AppriseTransport:
    async def send(
        self,
        channel: Channel,
        rendered: Rendered | None,
        event: Delivery,
        secrets: dict[str, str],
    ) -> SendResult:
        preset = preset_for(channel.preset)
        if preset is not None:
            # The URL exists only here and in the validation: built from the fields.
            try:
                urls = [compile_url(preset, {**channel.config, **secrets})]
            except PresetError:
                return SendResult("failed", error_code="not_configured")
            markdown = preset.format == "markdown"
        else:
            urls = split_apprise_urls(secrets.get("urls", ""))
            markdown = channel.config.get("format") == "markdown"
        if not urls:
            return SendResult("failed", error_code="not_configured")
        policy = channel.include_message_text
        if preset is not None and preset.text_policy_locked and policy != preset.text_policy:
            # Defence in depth: the API and the restore keep a locked policy, a row written
            # any other way still never sends a text an SMS service would cut.
            policy = preset.text_policy or "never"
        title, body = event_notification_text(
            dict(event.payload),
            await channel_language(channel),
            include_message_text=policy,
            markdown=markdown,
            template_dm=channel.template_dm,
            template_channel=channel.template_channel,
        )
        results = await send_to_urls(
            urls,
            allow_private_network=channel.allow_private_network,
            title=title,
            body=body,
            markdown=markdown,
            verify_tls=channel.verify_tls,
        )
        failures = [result for result in results if result.error is not None]
        for result in failures:
            assert result.error is not None
            logger.warning(
                "notification apprise %s: URL %d (%s) failed: %s",
                channel.id,
                result.line,
                result.url,
                result.error.code,
            )
        if not failures:
            return SEND_OK
        first = failures[0]
        assert first.error is not None
        return SendResult(
            "failed",
            error_code=f"line {first.line}: {getattr(first.error, 'delivery_code', first.error.code)}",
        )
