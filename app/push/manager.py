"""Web Push dispatch manager.

Conversation enablement uses ``policy.conversation_is_enabled`` plus a
separate muted-channel circuit breaker. First-seen contact alerts are
sent out-of-band to subscriptions and never go through WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pywebpush import WebPushException

from app.channel_constants import is_public_channel_key
from app.data.meshcore_channels import is_public_hashtag_channel
from app.net.egress_guard import EgressBlocked
from app.push.policy import conversation_is_enabled
from app.push.send import send_push
from app.push.vapid import get_vapid_claims, get_vapid_private_key
from app.repository.channels import ChannelRepository
from app.repository.push_subscriptions import PushSubscriptionRepository
from app.repository.settings import (
    NOTIFICATION_MEDIA_KEYS,
    AppSettingsRepository,
    any_media_enabled,
)

logger = logging.getLogger(__name__)

_SEND_TIMEOUT = 15  # seconds per push send

# Proof of modification: sent to every subscription, not configurable.
DESTINATION_CHANGED_EVENT = "notify.destination_changed"


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


def format_duration(seconds: object) -> str:
    """``45 s``, ``12 min``, ``2 h 05``: the same abbreviations in French and English."""
    try:
        total = max(int(float(str(seconds))), 0)
    except (TypeError, ValueError):
        total = 0
    if total < 60:
        return f"{total} s"
    if total < 3600:
        return f"{total // 60} min"
    return f"{total // 3600} h {total % 3600 // 60:02d}"


def _radio_where(data: dict) -> str:
    """The transport as shown to the user: ``USB /dev/ttyUSB0``, ``TCP host:port``, ``BLE addr``."""
    info = str(data.get("connection") or "").strip()
    kind, _, target = info.partition(": ")
    label = {"Serial": "USB"}.get(kind, kind)
    return f"{label} {target}".strip() if target else info


def _radio_status_titles(data: dict, lang: str) -> tuple[str, str]:
    where = _radio_where(data)
    suffix = f" ({where})" if where else ""
    event = data.get("event")
    if event == "radio_offline":
        duration = format_duration(data.get("offline_s"))
        if lang == "en":
            title = f"Radio offline for {duration}{suffix}"
            body = "The radio is unreachable. Meshloom keeps trying to reconnect."
        else:
            title = f"Radio hors ligne depuis {duration}{suffix}"
            body = "La radio est injoignable. Meshloom continue de se reconnecter."
        return title, body
    if event == "radio_online":
        duration = format_duration(data.get("downtime_s"))
        if lang == "en":
            return f"Radio back online after {duration}", f"The radio is reachable again{suffix}."
        return f"Radio de nouveau en ligne après {duration}", (
            f"La radio est de nouveau joignable{suffix}."
        )
    version = str(data.get("version") or "").strip()
    label = f" ({version})" if version else ""
    seconds = data.get("connect_s")
    connected = bool(data.get("radio_connected"))
    if lang == "en":
        title = f"Meshloom started{label}"
        if connected and seconds is not None:
            body = f"Radio connected in {format_duration(seconds)}"
        elif connected:
            body = "Radio connected"
        else:
            body = "The radio is not connected yet"
    else:
        title = f"Meshloom a démarré{label}"
        if connected and seconds is not None:
            body = f"Radio connectée en {format_duration(seconds)}"
        elif connected:
            body = "Radio connectée"
        else:
            body = "La radio n’est pas encore connectée"
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

    if data.get("event") in ("radio_offline", "radio_online", "app_started"):
        title, body = _radio_status_titles(data, lang)
        event = str(data["event"])
        return json.dumps(
            {
                "title": title,
                "body": body,
                # Online replaces the offline notification still in the tray.
                "tag": "meshcore-app-started"
                if event == "app_started"
                else "meshcore-radio-status",
                "url_hash": "#settings/radio",
            }
        )

    if data.get("event") in ("notifications_grouped", "first_seen_grouped"):
        count = int(data.get("count") or 0)
        if data["event"] == "first_seen_grouped":
            title = f"+{count} new contacts" if lang == "en" else f"+{count} nouveaux contacts"
        else:
            title = f"+{count} events" if lang == "en" else f"+{count} évènements"
        return json.dumps({"title": title, "body": title, "tag": "meshcore-grouped"})

    if data.get("event") == DESTINATION_CHANGED_EVENT:
        labels = {"email": "e-mail", "webhook": "webhook", "apprise": "Apprise"}
        kinds = ", ".join(labels.get(str(kind), str(kind)) for kind in data.get("kinds") or [])
        if lang == "en":
            title = "Notification destination changed"
            body = f"Changed: {kinds}. If this was not you, check the settings."
        else:
            title = "Destination de notification modifiée"
            body = f"Modifié : {kinds}. Si ce n'est pas vous, vérifiez les réglages."
        return json.dumps(
            {
                "title": title,
                "body": body,
                "tag": "meshcore-destination-changed",
                "url_hash": "#settings/notifications",
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


def _is_message(data: Mapping[str, Any]) -> bool:
    return data.get("event") in (None, "message") and data.get("type") in ("PRIV", "CHAN")


def message_text_is_public(data: Mapping[str, Any]) -> bool:
    """A Public or ``#`` channel whose key is derived from its name: anyone can read it."""
    if data.get("type") != "CHAN":
        return False
    key = str(data.get("conversation_key") or "")
    name = str(data.get("channel_name") or "")
    return is_public_channel_key(key) or is_public_hashtag_channel(name, key)


def _title_only_body(data: Mapping[str, Any], lang: str) -> str:
    sender = str(data.get("sender_name") or "").strip()
    if lang == "en":
        return f"Message from {sender}" if sender else "New message"
    return f"Message de {sender}" if sender else "Nouveau message"


# Discord, Slack, Mattermost, Rocket.Chat and Matrix room-wide pings, and the
# <@user> / <!channel> tokens of Discord and Slack.
_MENTION = re.compile(r"@(everyone|here|channel|all|room)\b|<[@!]", re.IGNORECASE)
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~<])")


def neutralise_mentions(text: str) -> str:
    """``@everyone``, ``@channel``, ``<@id>``, ``<!here>``... no longer ping: a
    zero-width space goes after ``@`` (or after ``<!`` and ``<@``)."""
    return _MENTION.sub(_break_mention, text)


def _break_mention(match: re.Match[str]) -> str:
    token = match.group(0)
    if token.startswith("<"):
        return f"{token}\u200b"
    return f"@\u200b{token[1:]}"


def escape_markdown(text: str) -> str:
    """User text shown as text: no injected link, heading or emphasis."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def event_notification_text(
    data: dict,
    language: str = "en",
    *,
    include_message_text: str = "always",
    markdown: bool = False,
) -> tuple[str, str]:
    """Title/body used by email and other non-push transports.

    ``include_message_text`` (the Apprise text policy) applies to messages
    only: ``public_only`` keeps the text of Public and ``#`` channels and
    sends a title-only body ("Message from Léa") for DMs and private
    channels; ``never`` does that everywhere. Mentions are always
    neutralised; ``markdown`` also escapes the text.
    """
    lang = language if language in ("fr", "en") else "fr"
    parsed = json.loads(_build_payload(data, lang))
    title = str(parsed.get("title") or "").strip() or "Meshloom"
    body = str(parsed.get("body") or "").strip() or title
    if _is_message(data) and (
        include_message_text == "never"
        or (include_message_text == "public_only" and not message_text_is_public(data))
    ):
        body = _title_only_body(data, lang)
    title, body = neutralise_mentions(title), neutralise_mentions(body)
    if markdown:
        title, body = escape_markdown(title), escape_markdown(body)
    return title, body


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
    # Refused by the egress guard: a configuration state, not a delivery failure.
    refused: bool = False
    # Refused for good (``EgressBlocked.permanent``): no setting can lift it.
    invalid: bool = False


class PushManager:
    def __init__(self) -> None:
        # (subscription id, code) already logged at WARNING: a liftable refusal
        # repeats on every dispatch until the setting or DNS changes.
        self._refusals_logged: set[tuple[str, str]] = set()

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
            if channel is not None and channel.membership == "pending":
                return

        flags = {
            media: conversation_is_enabled(
                state_key=state_key,
                message_type=msg_type,
                defaults=defaults,
                overrides=overrides,
                is_hashtag=is_hashtag,
                is_public=is_public,
                channel=media,
            )
            for media in NOTIFICATION_MEDIA_KEYS
        }
        if not any(flags.values()):
            return

        # Muted-channel circuit breaker — separate from conversation policy.
        if msg_type == "CHAN" and channel is not None and channel.muted:
            return

        if flags["apprise"]:
            from app.services.message_filters import is_blocked

            try:
                if is_blocked(data, await AppSettingsRepository.get()):
                    flags["apprise"] = False
            except Exception:
                logger.debug("Push dispatch: failed to load block lists", exc_info=True)
                flags["apprise"] = False

        from app.notify import dispatch_system_event

        payload = dict(data)
        payload.setdefault("event", "message")
        await dispatch_system_event(payload, flags)

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
        if not any_media_enabled(defaults, "oss_update"):
            return False
        from app.notify import dispatch_system_event

        await dispatch_system_event(
            {
                "event": "oss_update",
                "current": current,
                "latest": latest,
            },
            defaults["oss_update"],
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

        try:
            allow_private = (await AppSettingsRepository.get()).push_allow_private_endpoints
        except Exception:
            allow_private = False

        results = await asyncio.gather(
            *(self._send_one(sub, payload_for_sub(sub), vapid_key, allow_private) for sub in subs),
            return_exceptions=True,
        )
        await self._record_outcomes(results)

    async def _record_outcomes(self, results: list[Any]) -> None:
        success_ids: list[str] = []
        failure_ids: list[str] = []
        remove_ids: list[str] = []
        for r in results:
            if isinstance(r, _SendResult):
                if r.invalid:
                    remove_ids.append(r.sub_id)
                    continue
                if r.refused:
                    continue  # kept, and never counted toward the eviction threshold
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

    async def _send_one(
        self, sub: dict, payload: str, vapid_key: str, allow_private: bool = False
    ) -> _SendResult:
        sub_id = sub["id"]
        result = _SendResult(sub_id=sub_id)
        try:
            async with asyncio.timeout(_SEND_TIMEOUT):
                await send_push(
                    subscription_info=_subscription_info(sub),
                    payload=payload,
                    vapid_private_key=vapid_key,
                    vapid_claims=get_vapid_claims(),
                    allow_private_network=allow_private,
                )
            result.success = True
            self._refusals_logged = {k for k in self._refusals_logged if k[0] != sub_id}
        except EgressBlocked as e:
            result.refused = True
            if e.permanent:
                # A bad URL or an address forbidden whatever the setting: deleted.
                result.invalid = True
                logger.warning(
                    "Push endpoint refused for good by the egress guard (%s), removing %s",
                    e.code,
                    sub_id,
                )
            elif (sub_id, e.code) not in self._refusals_logged:
                # Kept and not counted as a failure: the endpoint becomes valid
                # again when the setting is ticked or DNS changes. Logged once.
                self._refusals_logged.add((sub_id, e.code))
                logger.warning("Push send refused by the egress guard for %s: %s", sub_id, e.code)
        except WebPushException as e:
            status = getattr(e, "response", None)
            status_code = getattr(status, "status_code", 0) if status else 0
            if status_code in (403, 404, 410):
                logger.info("Push subscription expired (HTTP %d), removing %s", status_code, sub_id)
                result.expired = True
            else:
                # The type only: a WebPushException carries the push service's body.
                logger.warning("Push send failed for %s: %s", sub_id, type(e).__name__)
        except TimeoutError:
            logger.warning("Push send timed out for %s", sub_id)
        except Exception as e:
            # The type only: a requests error names the endpoint URL (its path is a secret).
            logger.warning("Push send error for %s: %s", sub_id, type(e).__name__)
        return result


push_manager = PushManager()
