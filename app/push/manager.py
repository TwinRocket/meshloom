"""Web Push dispatch manager.

Conversation enablement uses ``policy.conversation_is_enabled`` plus a
separate muted-channel circuit breaker. First-seen contact alerts are
sent out-of-band to subscriptions and never go through WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pywebpush import WebPushException

from app.channel_constants import is_public_channel_key
from app.push.policy import conversation_is_enabled
from app.push.send import send_push
from app.push.vapid import get_vapid_claims, get_vapid_private_key
from app.repository.channels import ChannelRepository
from app.repository.push_subscriptions import PushSubscriptionRepository
from app.repository.settings import AppSettingsRepository

logger = logging.getLogger(__name__)

_SEND_TIMEOUT = 15  # seconds per push send


def _state_key_for_message(data: dict) -> str:
    """Derive the conversation state key from a message event payload."""
    msg_type = data.get("type", "")
    conversation_key = data.get("conversation_key", "")
    if msg_type == "PRIV":
        return f"contact-{conversation_key}"
    return f"channel-{conversation_key}"


def _first_seen_titles(data: dict, lang: str) -> tuple[str, str]:
    name = (data.get("name") or "").strip()
    contact_type = int(data.get("contact_type") or data.get("type") or 0)
    pubkey = str(data.get("public_key") or "")
    label = name or (pubkey[:12] if pubkey else "")

    if lang == "en":
        if contact_type == 2:
            title = f"New repeater: {label}" if label else "New repeater"
        elif contact_type == 4:
            title = f"New sensor: {label}" if label else "New sensor"
        else:
            title = f"New contact: {label}" if label else "New contact"
        body = f"{label} appeared on the mesh" if label else "First seen on the mesh"
    else:
        if contact_type == 2:
            title = f"Nouveau répéteur : {label}" if label else "Nouveau répéteur"
        elif contact_type == 4:
            title = f"Nouveau capteur : {label}" if label else "Nouveau capteur"
        else:
            title = f"Nouveau contact : {label}" if label else "Nouveau contact"
        body = f"{label} est apparu sur le mesh" if label else "Première apparition sur le mesh"
    return title, body


def _telemetry_alert_titles(data: dict, lang: str) -> tuple[str, str]:
    name = (data.get("name") or "").strip()
    pubkey = str(data.get("public_key") or "")
    label = name or (pubkey[:12] if pubkey else "")
    rule_id = str(data.get("rule_id") or "")
    value = data.get("value")
    threshold = data.get("threshold")
    kind = rule_id.split(":", 1)[0] if rule_id.startswith("lpp:") else rule_id

    def _fmt_value() -> str:
        if value is None:
            return "?"
        try:
            return f"{float(value):g}"
        except (TypeError, ValueError):
            return str(value)

    if lang == "en":
        if rule_id == "battery":
            title = f"Low battery: {label}" if label else "Low battery"
            body = f"{value:.2f} V (threshold {threshold} V)" if value is not None else title
        elif rule_id == "noise":
            title = f"High noise floor: {label}" if label else "High noise floor"
            body = f"{value} dBm (threshold {threshold} dBm)" if value is not None else title
        elif rule_id == "rssi":
            title = f"Low RSSI: {label}" if label else "Low RSSI"
            body = f"{value} dBm (threshold {threshold} dBm)" if value is not None else title
        elif rule_id == "snr":
            title = f"Low SNR: {label}" if label else "Low SNR"
            body = f"{value} dB (threshold {threshold} dB)" if value is not None else title
        elif rule_id == "tx_queue":
            title = f"TX queue high: {label}" if label else "TX queue high"
            body = f"{value} (threshold {threshold})" if value is not None else title
        elif rule_id == "silence":
            title = f"{label} is not responding" if label else "Node is not responding"
            body = f"No usable telemetry after {int(value) if value is not None else '?'} poll(s)"
        elif rule_id == "gps_lost":
            title = f"{label} lost GPS fix" if label else "GPS fix lost"
            body = title
        elif kind in {
            "temperature",
            "humidity",
            "barometer",
            "voltage",
            "current",
            "luminosity",
            "altitude",
            "power",
            "distance",
            "energy",
            "direction",
            "concentration",
        } or rule_id.startswith("lpp:"):
            metric = rule_id.removeprefix("lpp:") if rule_id.startswith("lpp:") else rule_id
            title = f"{metric} alert: {label}" if label else f"{metric} alert"
            body = f"{_fmt_value()} (threshold {threshold})" if threshold is not None else title
        else:
            title = f"{label}: {rule_id} alert" if label else f"{rule_id or 'Telemetry'} alert"
            body = f"{_fmt_value()} (threshold {threshold})" if threshold is not None else title
    else:
        if rule_id == "battery":
            title = f"Batterie faible : {label}" if label else "Batterie faible"
            body = f"{value:.2f} V (seuil {threshold} V)" if value is not None else title
        elif rule_id == "noise":
            title = f"Bruit élevé : {label}" if label else "Bruit élevé"
            body = f"{value} dBm (seuil {threshold} dBm)" if value is not None else title
        elif rule_id == "rssi":
            title = f"RSSI faible : {label}" if label else "RSSI faible"
            body = f"{value} dBm (seuil {threshold} dBm)" if value is not None else title
        elif rule_id == "snr":
            title = f"SNR faible : {label}" if label else "SNR faible"
            body = f"{value} dB (seuil {threshold} dB)" if value is not None else title
        elif rule_id == "tx_queue":
            title = f"File TX élevée : {label}" if label else "File TX élevée"
            body = f"{value} (seuil {threshold})" if value is not None else title
        elif rule_id == "silence":
            title = f"{label} ne répond plus" if label else "Nœud sans réponse"
            body = (
                f"Pas de télémétrie utilisable après "
                f"{int(value) if value is not None else '?'} sondage(s)"
            )
        elif rule_id == "gps_lost":
            title = f"{label} a perdu le GPS" if label else "GPS perdu"
            body = title
        elif kind in {
            "temperature",
            "humidity",
            "barometer",
            "voltage",
            "current",
            "luminosity",
            "altitude",
            "power",
            "distance",
            "energy",
            "direction",
            "concentration",
        } or rule_id.startswith("lpp:"):
            metric = rule_id.removeprefix("lpp:") if rule_id.startswith("lpp:") else rule_id
            title = f"Alerte {metric} : {label}" if label else f"Alerte {metric}"
            body = f"{_fmt_value()} (seuil {threshold})" if threshold is not None else title
        else:
            title = f"{label} : alerte {rule_id}" if label else f"Alerte {rule_id or 'télémétrie'}"
            body = f"{_fmt_value()} (seuil {threshold})" if threshold is not None else title
    return title, body


def _build_payload(data: dict, language: str = "fr") -> str:
    """Build the push notification JSON payload from a message or first-seen event."""
    lang = language if language in ("fr", "en") else "fr"

    if data.get("event") == "channel_found":
        name = (data.get("name") or "").strip()
        key = str(data.get("channel_key") or "")
        if lang == "en":
            title = f"New channel detected: {name}" if name else "New channel detected"
        else:
            title = f"Nouveau canal détecté : {name}" if name else "Nouveau canal détecté"
        return json.dumps(
            {
                "title": title,
                "body": title,
                "tag": f"meshcore-channel-found-{name or key}",
                "url_hash": f"#channel/{key}" if key else "",
            }
        )

    if data.get("event") == "oss_update":
        current = str(data.get("current") or "")
        latest = str(data.get("latest") or "")
        if lang == "en":
            title = "Meshloom update available"
            body = f"{latest} is available (you have {current})"
        else:
            title = "Mise à jour Meshloom disponible"
            body = f"La version {latest} est disponible (vous avez {current})"
        return json.dumps(
            {
                "title": title,
                "body": body,
                "tag": f"meshcore-oss-update-{latest}",
                "url_hash": "#settings/updates",
            }
        )

    if data.get("event") == "telemetry_alert":
        title, body = _telemetry_alert_titles(data, lang)
        pubkey = str(data.get("public_key") or "")
        rule_id = str(data.get("rule_id") or "")
        return json.dumps(
            {
                "title": title,
                "body": body,
                "tag": f"meshcore-telemetry-{rule_id}-{pubkey}",
                "url_hash": f"#contact/{pubkey}" if pubkey else "",
            }
        )

    if data.get("event") == "first_seen":
        title, body = _first_seen_titles(data, lang)
        pubkey = str(data.get("public_key") or "")
        return json.dumps(
            {
                "title": title,
                "body": body,
                "tag": f"meshcore-first-seen-{pubkey}",
                "url_hash": f"#contact/{pubkey}" if pubkey else "",
            }
        )

    msg_type = data.get("type", "")
    text = data.get("text", "")
    sender_name = data.get("sender_name") or ""
    channel_name = data.get("channel_name") or ""

    if msg_type == "PRIV":
        if lang == "en":
            title = f"Message from {sender_name}" if sender_name else "New direct message"
        else:
            title = f"Message de {sender_name}" if sender_name else "Nouveau message privé"
        body = text
    else:
        if lang == "en":
            title = channel_name if channel_name else "Channel message"
        else:
            title = channel_name if channel_name else "Message de canal"
        body = text

    conversation_key = data.get("conversation_key", "")
    state_key = _state_key_for_message(data)
    if msg_type == "PRIV":
        url_hash = f"#contact/{conversation_key}"
    else:
        url_hash = f"#channel/{conversation_key}"

    return json.dumps(
        {
            "title": title,
            "body": body,
            # Tag per conversation so different conversations coexist in the
            # notification tray, while repeated messages in the same
            # conversation replace each other.
            "tag": f"meshcore-{state_key}",
            "url_hash": url_hash,
        }
    )


def _subscription_info(sub: dict) -> dict:
    """Build the subscription_info dict that pywebpush expects."""
    return {
        "endpoint": sub["endpoint"],
        "keys": {
            "p256dh": sub["p256dh"],
            "auth": sub["auth"],
        },
    }


@dataclass
class _SendResult:
    sub_id: str
    success: bool = False
    expired: bool = False


class PushManager:
    async def dispatch_message(self, data: dict) -> None:
        """Send push notifications for a message event to all devices."""
        # Don't notify for messages the operator just sent themselves
        if data.get("outgoing"):
            return

        state_key = _state_key_for_message(data)
        msg_type = data.get("type", "")
        conversation_key = str(data.get("conversation_key") or "")

        try:
            defaults = await AppSettingsRepository.get_push_defaults()
            overrides = await AppSettingsRepository.get_push_conversation_overrides()
        except Exception:
            logger.debug("Push dispatch: failed to load push preferences", exc_info=True)
            return

        is_hashtag = False
        is_public = False
        channel = None
        if msg_type == "CHAN" and conversation_key:
            is_public = is_public_channel_key(conversation_key)
            try:
                channel = await ChannelRepository.get_by_key(conversation_key)
            except Exception:
                logger.debug("Push dispatch: failed to load channel", exc_info=True)
            if channel is not None:
                is_hashtag = bool(channel.is_hashtag)

        if not conversation_is_enabled(
            state_key=state_key,
            message_type=msg_type,
            defaults=defaults,
            overrides=overrides,
            is_hashtag=is_hashtag,
            is_public=is_public,
        ):
            return

        # Muted-channel circuit breaker — separate from conversation policy.
        if msg_type == "CHAN" and channel is not None and channel.muted:
            return

        await self._send_to_all_subscriptions(
            lambda sub: _build_payload(data, sub.get("language") or "fr")
        )

    async def dispatch_event(self, data: dict) -> None:
        """Send a push payload to all subscriptions. Not a WebSocket event."""
        await self._send_to_all_subscriptions(
            lambda sub: _build_payload(data, sub.get("language") or "fr")
        )

    async def dispatch_first_seen(self, contact: Mapping[str, Any]) -> None:
        """Send a first-seen contact alert to all subscriptions (no WebSocket)."""
        public_key = str(contact.get("public_key") or "")
        await self.dispatch_event(
            {
                "event": "first_seen",
                "public_key": public_key,
                "name": contact.get("name") or "",
                "contact_type": contact.get("type", 0),
            }
        )

    async def dispatch_oss_update(self, current: str, latest: str) -> bool:
        """Notify devices that a newer Meshloom release is available (no WebSocket).

        Returns True when the default is on and a send was attempted (even with
        zero subscriptions). Returns False when ``oss_update`` is disabled.
        """
        try:
            defaults = await AppSettingsRepository.get_push_defaults()
        except Exception:
            logger.debug("Push dispatch: failed to load push defaults", exc_info=True)
            return False
        if not defaults.get("oss_update", True):
            return False
        await self.dispatch_event(
            {
                "event": "oss_update",
                "current": current,
                "latest": latest,
            }
        )
        return True

    async def _send_to_all_subscriptions(self, payload_for_sub: Callable[[dict], str]) -> None:
        try:
            subs = await PushSubscriptionRepository.get_all()
        except Exception:
            logger.debug("Push dispatch: failed to load subscriptions", exc_info=True)
            return

        if not subs:
            return

        vapid_key = get_vapid_private_key()
        if not vapid_key:
            logger.debug("Push dispatch: no VAPID key configured, skipping")
            return

        results = await asyncio.gather(
            *(self._send_one(sub, payload_for_sub(sub), vapid_key) for sub in subs),
            return_exceptions=True,
        )
        await self._record_outcomes(results)

    async def _record_outcomes(self, results: list[Any]) -> None:
        success_ids: list[str] = []
        failure_ids: list[str] = []
        remove_ids: list[str] = []
        for r in results:
            if isinstance(r, _SendResult):
                if r.expired:
                    remove_ids.append(r.sub_id)
                elif r.success:
                    success_ids.append(r.sub_id)
                else:
                    failure_ids.append(r.sub_id)
        if success_ids or failure_ids or remove_ids:
            try:
                await PushSubscriptionRepository.batch_record_outcomes(
                    success_ids, failure_ids, remove_ids
                )
            except Exception:
                logger.debug("Push dispatch: failed to record outcomes", exc_info=True)

    async def _send_one(self, sub: dict, payload: str, vapid_key: str) -> _SendResult:
        sub_id = sub["id"]
        result = _SendResult(sub_id=sub_id)
        try:
            async with asyncio.timeout(_SEND_TIMEOUT):
                await send_push(
                    subscription_info=_subscription_info(sub),
                    payload=payload,
                    vapid_private_key=vapid_key,
                    vapid_claims=get_vapid_claims(),
                )
            result.success = True
        except WebPushException as e:
            status = getattr(e, "response", None)
            status_code = getattr(status, "status_code", 0) if status else 0
            if status_code in (403, 404, 410):
                logger.info("Push subscription expired (HTTP %d), removing %s", status_code, sub_id)
                result.expired = True
            else:
                logger.warning("Push send failed for %s: %s", sub_id, e)
        except TimeoutError:
            logger.warning("Push send timed out for %s", sub_id)
        except Exception:
            logger.debug("Push send error for %s", sub_id, exc_info=True)
        return result


push_manager = PushManager()
