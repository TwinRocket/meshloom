"""The 4.18.x media as transports, until their native transports land (MSHL-110).

``legacy:email``, ``legacy:webhook`` and ``legacy:apprise`` still read their
destination from ``app_settings.notification_destinations`` at send time, through
the functions of ``app/notify.py`` they wrapped before the dispatcher: the
destinations, the texts and the egress checks are unchanged. What the
dispatcher adds is the worker per channel, the limiter, the retry policy (the
webhook only: e-mail gives no status, and the Apprise medium retries each URL
itself, so a second layer would resend to the URLs that took it) and the status.
"""

from __future__ import annotations

import logging

from app.notifications.apprise_medium import AppriseSendFailed
from app.notifications.channels import Channel
from app.notifications.render import Rendered
from app.notifications.transports.base import (
    SEND_OK,
    Delivery,
    SendResult,
    result_from_exception,
)

logger = logging.getLogger(__name__)


async def _destinations():
    from app.repository.settings import AppSettingsRepository

    return (await AppSettingsRepository.get()).notification_destinations


class LegacyEmailTransport:
    async def send(
        self,
        channel: Channel,
        rendered: Rendered | None,
        event: Delivery,
        secrets: dict[str, str],
    ) -> SendResult:
        from app import notify

        try:
            await notify.send_email_alert(await _destinations(), event.payload)
        except ValueError:
            return SendResult("failed", error_code="not_configured")
        except Exception as exc:
            logger.warning("notify: email dispatch failed: %s", type(exc).__name__)
            return SendResult("failed", error_code="send_failed")
        return SEND_OK


class LegacyWebhookTransport:
    async def send(
        self,
        channel: Channel,
        rendered: Rendered | None,
        event: Delivery,
        secrets: dict[str, str],
    ) -> SendResult:
        from app import notify

        dest = await _destinations()
        try:
            await notify.send_webhook_alert(
                dest,
                event.payload,
                dedup_key=event.event.dedup_key if event.event is not None else "",
            )
        except ValueError:
            return SendResult("failed", error_code="not_configured")
        except Exception as exc:
            logger.warning(
                "notify: webhook dispatch failed: %s",
                notify.describe_webhook_failure(exc, dest.webhook.url or ""),
            )
            return result_from_exception(exc)
        return SEND_OK


class LegacyAppriseTransport:
    async def send(
        self,
        channel: Channel,
        rendered: Rendered | None,
        event: Delivery,
        secrets: dict[str, str],
    ) -> SendResult:
        from app import notify
        from app.net.apprise_urls import AppriseUrlRefused

        try:
            await notify.send_apprise_alert(await _destinations(), event.payload)
        except ValueError:
            return SendResult("failed", error_code="not_configured")
        except AppriseUrlRefused as exc:
            return SendResult("failed", error_code=f"line {exc.line}: {exc.code}")
        except AppriseSendFailed as exc:
            # Logged per URL by send_apprise_alert; the URL here is redacted.
            return SendResult("failed", error_code=f"line {exc.line} ({exc.url}): {exc.code}")
        return SEND_OK
