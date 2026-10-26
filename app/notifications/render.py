"""The one FR/EN renderer behind every notification transport.

Push payloads, e-mail (subject, plain and HTML bodies), the webhook body and
the Apprise text all come from this module, so the same event reads the same
everywhere. The copy was moved here unchanged from ``app/push/manager.py`` and
``app/email_template.py``; ``tests/test_notifications_golden.py`` pins the
output byte for byte. The typed entry point is ``render()``; the dict helpers
(``push_payload``, ``event_notification_text``, ``notification_email_lines``...)
serve the producers that still pass the legacy payload dicts, which
``app/notifications/events.py`` converts into catalogue events.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, Any, Literal

from app.channel_constants import is_public_channel_key
from app.data.meshcore_channels import is_public_hashtag_channel
from app.fanout.base import get_fanout_message_text
from app.notifications.markdown import escape_markdown, neutralise_mentions
from app.notifications.templates import apply_template, build_variables, template_for
from app.share_text import share_text_summary

if TYPE_CHECKING:
    from app.notifications.events import NotificationEvent

# Inline logo name: the HTML refers to it by ``cid:`` (``app/email_template.py``).
LOGO_CID = "meshloom-mark.png"

AppLanguage = Literal["fr", "en"]

# Proof of modification: sent to every subscription, not configurable.
DESTINATION_CHANGED_EVENT = "notify.destination_changed"
CHANNEL_CHANGED_EVENT = "notify.channel_changed"
# A channel keeps failing, or a push service dropped a browser (``failures.py``).
CHANNEL_FAILED_EVENT = "notify.channel_failed"
# The periodic "still running" message of a channel with a heartbeat (``heartbeat.py``).
HEARTBEAT_EVENT = "notify.heartbeat"


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


def state_key_for_message(data: dict) -> str:
    """Derive the conversation state key from a message event payload."""
    msg_type = data.get("type", "")
    conversation_key = data.get("conversation_key", "")
    if msg_type == "PRIV":
        return f"contact-{conversation_key}"
    return f"channel-{conversation_key}"


def _grouped_title(data: dict, count: int, lang: str) -> str:
    """ "+14 messages: 12 in #paris, 2 DMs": counts and open-channel names, never a text.
    Without the breakdown (older callers) it is "+N events"."""
    en = lang == "en"
    channels = data.get("channels") or {}
    dms = int(data.get("dms") or 0)
    private = int(data.get("private") or 0)
    others = int(data.get("others") or 0)
    if not (channels or dms or private):
        return f"+{count} events" if en else f"+{count} évènements"
    parts = [f"{n} in {name}" if en else f"{n} dans {name}" for name, n in channels.items()]
    if private:
        parts.append(f"{private} in other channels" if en else f"{private} dans d'autres canaux")
    if dms:
        parts.append(f"{dms} DMs" if en else f"{dms} MP")
    if others:
        parts.append(f"{others} other events" if en else f"{others} autres évènements")
    messages = count - others
    head = f"+{messages} messages" if en else f"+{messages} messages"
    return f"{head}: {', '.join(parts)}"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def quiet_digest_text(data: Mapping[str, Any], lang: str) -> tuple[str, str]:
    """Title and body of the digest of what quiet hours (or a pause) held back:
    "During quiet hours: 14 messages in 3 conversations (#paris 9, Léa 3, #test 2), 1 new
    contact". Counts and names only, never a message text."""
    en = lang == "en"
    pause = data.get("scope") == "pause"
    messages = int(data.get("count") or 0)
    conversations = int(data.get("conversations") or 0)
    contacts = int(data.get("contacts") or 0)
    others = int(data.get("others") or 0)
    dms = int(data.get("dms") or 0)
    private = int(data.get("private") or 0)
    more = int(data.get("more") or 0)
    detail = [f"{name} {n}" for name, n in (data.get("items") or [])]
    if more:
        detail.append(f"{more} more" if en else f"{more} autres")
    if dms:
        detail.append(f"{dms} DMs" if en else f"{dms} MP")
    if private:
        detail.append(f"{private} in private channels" if en else f"{private} en canaux privés")
    parts: list[str] = []
    if messages:
        head = _plural(messages, "message", "messages") + (" in " if en else " dans ")
        head += _plural(conversations, "conversation", "conversations")
        parts.append(f"{head} ({', '.join(detail)})" if detail else head)
    if contacts:
        parts.append(
            _plural(contacts, "new contact", "new contacts")
            if en
            else _plural(contacts, "nouveau contact", "nouveaux contacts")
        )
    if others:
        parts.append(
            _plural(others, "other event", "other events")
            if en
            else _plural(others, "autre évènement", "autres évènements")
        )
    if en:
        title = "Summary of the pause" if pause else "Quiet hours summary"
        lead = "During the pause: " if pause else "During quiet hours: "
    else:
        title = "Résumé de la pause" if pause else "Résumé des heures calmes"
        lead = "Pendant la pause : " if pause else "Pendant les heures calmes : "
    return title, lead + ", ".join(parts)


def build_push_payload(data: dict, language: str = "fr") -> str:
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
            title = _grouped_title(data, count, lang)
        return json.dumps({"title": title, "body": title, "tag": "meshcore-grouped"})

    if data.get("event") == "quiet_digest":
        title, body = quiet_digest_text(data, lang)
        return json.dumps(
            {"title": title, "body": body, "tag": "meshcore-quiet-digest", "url_hash": ""}
        )

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

    if data.get("event") == CHANNEL_CHANGED_EVENT:
        action = str(data.get("action") or "changed")
        name = str(data.get("name") or "")
        verbs = {
            "en": {
                "created": "created",
                "changed": "modified",
                "enabled": "enabled",
                "disabled": "disabled",
                "deleted": "deleted",
            },
            "fr": {
                "created": "créé",
                "changed": "modifié",
                "enabled": "activé",
                "disabled": "désactivé",
                "deleted": "supprimé",
            },
        }[lang if lang == "en" else "fr"]
        verb = verbs.get(action, verbs["changed"])
        if lang == "en":
            title = "Notification channel changed"
            body = f'Channel "{name}" {verb}. If this was not you, check the settings.'
        else:
            title = "Canal de notification modifié"
            body = f"Canal « {name} » {verb}. Si ce n'est pas vous, vérifiez les réglages."
        return json.dumps(
            {
                "title": title,
                "body": body,
                "tag": "meshcore-channel-changed",
                "url_hash": "#settings/notifications",
            }
        )

    if data.get("event") == HEARTBEAT_EVENT:
        instance = str(data.get("instance") or "Meshloom")
        uptime = format_duration(data.get("uptime_s"))
        version = str(data.get("version") or "")
        if lang == "en":
            title = f"{instance} is running"
            body = f"Up for {uptime}" + (f", version {version}." if version else ".")
        else:
            title = f"{instance} fonctionne"
            body = f"En service depuis {uptime}" + (f", version {version}." if version else ".")
        return json.dumps(
            {"title": title, "body": body, "tag": "meshcore-heartbeat", "url_hash": ""}
        )

    if data.get("event") == CHANNEL_FAILED_EVENT:
        name = str(data.get("name") or "")
        error = str(data.get("error") or "send_failed")
        if error == "push_unsubscribed":
            if lang == "en":
                title = "Browser unsubscribed"
                body = (
                    f'Browser "{name}" was unsubscribed by its push service. '
                    "Turn notifications on again on that device."
                )
            else:
                title = "Navigateur désabonné"
                body = (
                    f"Le navigateur « {name} » a été désabonné par son service push. "
                    "Réactivez les notifications sur cet appareil."
                )
        elif lang == "en":
            title = "Notification channel failing"
            body = f'Channel "{name}" keeps failing ({error}). Check it in the settings.'
        else:
            title = "Canal de notification en échec"
            body = (
                f"Le canal « {name} » échoue sans cesse ({error}). Vérifiez-le dans les réglages."
            )
        return json.dumps(
            {
                "title": title,
                "body": body,
                "tag": f"meshcore-channel-failed-{data.get('channel_id') or ''}",
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
    text = message_body_text(data.get("text", ""), lang, data.get("sender_name"))
    sender_name = data.get("sender_name") or ""
    channel_name = data.get("channel_name") or ""

    if data.get("mention") and msg_type == "CHAN":
        where = channel_name or ("this channel" if lang == "en" else "ce canal")
        if lang == "en":
            title = f"Mention in {where} by {sender_name}" if sender_name else f"Mention in {where}"
        else:
            title = (
                f"Mention dans {where} par {sender_name}"
                if sender_name
                else f"Mention dans {where}"
            )
        body = text
    elif msg_type == "PRIV":
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
    state_key = state_key_for_message(data)
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


def message_body_text(text: object, lang: str, sender_name: object = "") -> str:
    """A message text as a notification shows it: a share link becomes its summary.

    The one call site of ``share_text_summary`` (MSHL-250): push, the e-mail lines,
    the Apprise text and the ntfy body all go through it, so none shows a bare URL
    or a ``#fragment``. Offline: nothing is fetched. A channel message is stored
    as ``"Name: body"``: the prefix is split off (the known ``sender_name``, else
    the text before the first ``": "``), the body summarised, the prefix put back
    (``"Léa: [image]"``). Other texts are unchanged.
    """
    raw = str(text or "")
    direct = share_text_summary(raw, lang)
    if direct:
        return direct
    name = str(sender_name or "")
    if name and raw.startswith(f"{name}: "):
        prefix, rest = f"{name}: ", raw[len(name) + 2 :]
    elif ": " in raw and len(raw.split(": ", 1)[0]) <= 64:
        head, rest = raw.split(": ", 1)
        prefix, rest = f"{head}: ", rest
    else:
        return raw
    summary = share_text_summary(rest, lang)
    return f"{prefix}{summary}" if summary else raw


def _is_message(data: Mapping[str, Any]) -> bool:
    return data.get("event") in (None, "message") and data.get("type") in ("PRIV", "CHAN")


def message_text_is_public(data: Mapping[str, Any]) -> bool:
    """A Public or ``#`` channel whose key is derived from its name: anyone can read it."""
    # 'public' lets the text leave in notifications, so the stricter '#'-name rule is intentional.
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


def event_notification_text(
    data: dict,
    language: str = "en",
    *,
    include_message_text: str = "always",
    markdown: bool = False,
    template_dm: str = "",
    template_channel: str = "",
) -> tuple[str, str]:
    """Title/body used by email and other non-push transports.

    ``include_message_text`` (the Apprise text policy) applies to messages
    only: ``public_only`` keeps the text of Public and ``#`` channels and
    sends a title-only body ("Message from Léa") for DMs and private
    channels; ``never`` does that everywhere. Mentions are always
    neutralised; ``markdown`` also escapes the text.

    ``template_dm``/``template_channel`` (the channel's templates, ``templates.py``) replace
    the body of a message event when non-empty: the text policy still decides what ``{text}``
    holds, and a template that renders empty falls back to the default body.
    """
    lang = language if language in ("fr", "en") else "fr"
    parsed = json.loads(build_push_payload(data, lang))
    title = str(parsed.get("title") or "").strip() or "Meshloom"
    body = str(parsed.get("body") or "").strip() or title
    if _is_message(data) and (
        include_message_text == "never"
        or (include_message_text == "public_only" and not message_text_is_public(data))
    ):
        body = _title_only_body(data, lang)
        text_stripped = True
    else:
        text_stripped = False
    raw_title = title
    title, body = neutralise_mentions(title), neutralise_mentions(body)
    if markdown:
        title, body = escape_markdown(title), escape_markdown(body)
    template = template_for(data, template_dm, template_channel) if _is_message(data) else ""
    if template.strip():
        text = (
            ""
            if text_stripped
            else message_body_text(
                get_fanout_message_text(data), lang, data.get("sender_name") or ""
            )
        )
        variables = build_variables(data, title=raw_title, text=text, markdown=markdown)
        # A template that renders empty (only ``{text}`` under a stripping policy) keeps the
        # default body.
        body = neutralise_mentions(apply_template(template, variables)).strip() or body
    return title, body


_RULE_UNITS: dict[str, str] = {
    "battery": "V",
    "noise": "dBm",
    "rssi": "dBm",
    "snr": "dB",
    "tx_queue": "",
    "silence": "",
    "gps_lost": "",
    "test": "V",
    "temperature": "°C",
    "humidity": "%",
    "barometer": "hPa",
    "voltage": "V",
    "current": "A",
    "luminosity": "lux",
    "altitude": "m",
    "power": "W",
    "distance": "m",
    "energy": "kWh",
    "direction": "°",
    "concentration": "ppm",
}

_RULE_LABELS: dict[AppLanguage, dict[str, str]] = {
    "fr": {
        "battery": "Tension de la batterie",
        "noise": "Plancher de bruit",
        "rssi": "Dernier RSSI",
        "snr": "Dernier SNR",
        "tx_queue": "File TX",
        "silence": "Sondages manqués",
        "gps_lost": "GPS perdu",
        "test": "Tension de la batterie",
        "temperature": "Température",
        "humidity": "Humidité",
        "barometer": "Baromètre",
        "voltage": "Tension",
        "current": "Courant",
        "luminosity": "Luminosité",
        "altitude": "Altitude",
        "power": "Puissance",
        "distance": "Distance",
        "energy": "Énergie",
        "direction": "Direction",
        "concentration": "Concentration",
    },
    "en": {
        "battery": "Battery voltage",
        "noise": "Noise floor",
        "rssi": "Last RSSI",
        "snr": "Last SNR",
        "tx_queue": "TX queue",
        "silence": "Missed polls",
        "gps_lost": "GPS lost",
        "test": "Battery voltage",
        "temperature": "Temperature",
        "humidity": "Humidity",
        "barometer": "Barometer",
        "voltage": "Voltage",
        "current": "Current",
        "luminosity": "Luminosity",
        "altitude": "Altitude",
        "power": "Power",
        "distance": "Distance",
        "energy": "Energy",
        "direction": "Direction",
        "concentration": "Concentration",
    },
}

_COPY: dict[AppLanguage, dict[str, str]] = {
    "fr": {
        "intro_alert": "Une alerte a été déclenchée concernant : {name}",
        "threshold": "Seuil réglé : {op} {value}",
        "op_lt": "inférieur à",
        "op_gt": "supérieur à",
        "footer": "Envoyé par Meshloom",
        "intro_first_seen": "Un nouveau nœud est apparu sur le mesh.",
        "intro_channel": "Un nouveau canal hashtag a été trouvé.",
        "intro_update": "Une mise à jour Meshloom est disponible.",
        "intro_message": "Nouveau message",
        "intro_generic": "Notification Meshloom",
        "current_version": "Version actuelle",
        "latest_version": "Nouvelle version",
        "node_type_1": "Compagnon",
        "node_type_2": "Répéteur",
        "node_type_4": "Capteur",
        "sender": "Expéditeur",
        "channel": "Canal",
        "type_label": "Type",
    },
    "en": {
        "intro_alert": "An alert was raised for: {name}",
        "threshold": "Configured threshold: {op} {value}",
        "op_lt": "below",
        "op_gt": "above",
        "footer": "Sent by Meshloom",
        "intro_first_seen": "A new node appeared on the mesh.",
        "intro_channel": "A new hashtag channel was found.",
        "intro_update": "A Meshloom update is available.",
        "intro_message": "New message",
        "intro_generic": "Meshloom notification",
        "current_version": "Current version",
        "latest_version": "New version",
        "node_type_1": "Companion",
        "node_type_2": "Repeater",
        "node_type_4": "Sensor",
        "sender": "Sender",
        "channel": "Channel",
        "type_label": "Type",
    },
}


def normalize_email_language(raw: object) -> AppLanguage:
    return "en" if str(raw or "").lower().startswith("en") else "fr"


def rule_kind(rule_id: str) -> str:
    raw = (rule_id or "").strip()
    if raw.startswith("lpp:"):
        return raw.split(":", 2)[1] or raw
    return raw or "telemetry"


def enrich_notification_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Add explicit node/metric fields so webhook consumers need not infer them."""
    out = dict(payload)
    if str(out.get("event") or "") != "telemetry_alert":
        return out
    rule_id = str(out.get("rule_id") or "")
    kind = rule_kind(rule_id)
    out["metric"] = kind
    unit = _metric_unit(rule_id)
    if unit:
        out["unit"] = unit
    return out


def _fmt_number(raw: object) -> str:
    if raw is None or raw == "":
        return "—"
    try:
        value = float(str(raw))
    except (TypeError, ValueError):
        return str(raw)
    if value.is_integer() and abs(value) < 1_000_000:
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _with_unit(value: str, unit: str) -> str:
    return f"{value} {unit}".strip()


def _node_label(payload: Mapping[str, Any]) -> str:
    name = str(payload.get("name") or "").strip()
    pubkey = str(payload.get("public_key") or "")
    return name or (pubkey[:12] if pubkey else "Meshloom")


def _copy(lang: AppLanguage) -> dict[str, str]:
    return _COPY[lang]


def _metric_label(rule_id: str, lang: AppLanguage) -> str:
    kind = rule_kind(rule_id)
    return _RULE_LABELS[lang].get(kind, kind)


def _metric_unit(rule_id: str) -> str:
    return _RULE_UNITS.get(rule_kind(rule_id), "")


def _threshold_phrase(payload: Mapping[str, Any], lang: AppLanguage) -> str | None:
    threshold = payload.get("threshold")
    if threshold is None or threshold == "":
        return None
    op = str(payload.get("op") or "")
    copy = _copy(lang)
    op_words = copy["op_lt"] if op == "lt" else copy["op_gt"] if op == "gt" else ""
    value = _with_unit(_fmt_number(threshold), _metric_unit(str(payload.get("rule_id") or "")))
    if not op_words:
        return copy["threshold"].format(op="", value=value).replace("  ", " ")
    return copy["threshold"].format(op=op_words, value=value)


def telemetry_email_lines(payload: Mapping[str, Any], lang: AppLanguage) -> tuple[str, list[str]]:
    copy = _copy(lang)
    name = _node_label(payload)
    intro = copy["intro_alert"].format(name=name)
    rule_id = str(payload.get("rule_id") or "")
    kind = rule_kind(rule_id)
    lines: list[str] = []
    if kind == "gps_lost":
        lines.append(_metric_label(rule_id, lang))
    elif kind == "silence":
        polls = _fmt_number(payload.get("value"))
        lines.append(f"{_metric_label(rule_id, lang)} : {polls}")
        threshold = _threshold_phrase(payload, lang)
        if threshold:
            lines.append(f"({threshold})")
    else:
        reading = _with_unit(_fmt_number(payload.get("value")), _metric_unit(rule_id))
        lines.append(f"{_metric_label(rule_id, lang)} : {reading}")
        threshold = _threshold_phrase(payload, lang)
        if threshold:
            lines.append(f"({threshold})")
    return intro, lines


def notification_email_lines(
    payload: Mapping[str, Any], lang: AppLanguage
) -> tuple[str, list[str]]:
    event = str(payload.get("event") or "")
    copy = _copy(lang)
    if event == "telemetry_alert":
        return telemetry_email_lines(payload, lang)
    if event == "first_seen":
        name = _node_label(payload)
        contact_type = int(payload.get("contact_type") or payload.get("type") or 0)
        type_label = copy.get(f"node_type_{contact_type}", "")
        lines = [name]
        if type_label:
            lines.append(f"{copy['type_label']} : {type_label}")
        return copy["intro_first_seen"], lines
    if event == "channel_found":
        name = str(payload.get("name") or "").strip() or str(payload.get("channel_key") or "")
        return copy["intro_channel"], [name] if name else []
    if event == "oss_update":
        current = str(payload.get("current") or "").strip()
        latest = str(payload.get("latest") or "").strip()
        lines: list[str] = []
        if latest:
            lines.append(f"{copy['latest_version']} : {latest}")
        if current:
            lines.append(f"{copy['current_version']} : {current}")
        return copy["intro_update"], lines
    if event == "message" or payload.get("type") in ("PRIV", "CHAN"):
        sender = str(payload.get("sender_name") or "").strip()
        channel = str(payload.get("channel_name") or "").strip()
        text = message_body_text(
            str(payload.get("text") or "").strip(), lang, payload.get("sender_name")
        )
        lines = []
        if sender:
            lines.append(f"{copy['sender']} : {sender}")
        if channel:
            lines.append(f"{copy['channel']} : {channel}")
        if text:
            lines.append(text)
        return copy["intro_message"], lines
    title, body = event_notification_text(dict(payload), lang)
    lines = [body] if body and body != title else []
    return title or copy["intro_generic"], lines


def render_plain_email(payload: Mapping[str, Any], lang: AppLanguage) -> str:
    intro, lines = notification_email_lines(payload, lang)
    parts = [intro, "", *lines] if lines else [intro]
    return "\n".join(part for part in parts if part is not None)


def render_html_email(payload: Mapping[str, Any], lang: AppLanguage) -> str:
    intro, lines = notification_email_lines(payload, lang)
    copy = _copy(lang)
    detail_html = "".join(
        f'<p style="margin:0 0 8px 0;font-size:16px;line-height:1.5;color:#e8e8ed;">{escape(line)}</p>'
        for line in lines
    )
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<body style="margin:0;padding:0;background:#07080c;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#07080c;padding:24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#111318;border:1px solid #2a2d36;border-radius:16px;">
          <tr>
            <td style="padding:22px 24px 18px 24px;border-bottom:1px solid #2a2d36;">
              <table role="presentation" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="vertical-align:middle;padding-right:12px;">
                    <img src="cid:{escape(LOGO_CID, quote=True)}" width="40" height="33" alt="Meshloom" style="display:block;border:0;width:40px;height:auto;" />
                  </td>
                  <td style="vertical-align:middle;font-family:Arial,Helvetica,sans-serif;font-size:18px;font-weight:700;letter-spacing:0.04em;color:#f4f4f5;">
                    Meshloom
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:24px;font-family:Arial,Helvetica,sans-serif;color:#f4f4f5;">
              <p style="margin:0 0 16px 0;font-size:16px;line-height:1.5;color:#c4c4cc;">{escape(intro)}</p>
              {detail_html}
            </td>
          </tr>
          <tr>
            <td style="padding:16px 24px 22px 24px;border-top:1px solid #2a2d36;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#8b8b96;">
              {escape(copy["footer"])}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


TextPolicy = Literal["always", "public_only", "never"]


@dataclass(frozen=True)
class Rendered:
    """One event, rendered once for every transport.

    ``body_html`` is the whole HTML e-mail document (the intro and detail lines
    are ``html.escape``d); ``body_markdown`` escapes user text; ``tag`` and
    ``url_hash`` are the push tag (replaces a notification of the same
    conversation) and the in-app link ("" when there is none).
    """

    title: str
    body_text: str
    body_markdown: str
    body_html: str
    tag: str
    url_hash: str


def render(event: NotificationEvent, lang: str, *, text_policy: TextPolicy = "always") -> Rendered:
    """Render a catalogue event in ``lang`` (``fr`` or ``en``; anything else is French).

    ``text_policy`` is the ``include_message_text`` policy of the Apprise text,
    applied here once, to messages only: ``public_only`` keeps the text of Public
    and ``#`` channels and gives DMs and private channels a title-only body.
    Mention tokens are neutralised in every output.
    """
    language: AppLanguage = "en" if lang == "en" else "fr"
    payload = event.to_payload()
    title, body_text = event_notification_text(payload, language, include_message_text=text_policy)
    _, body_markdown = event_notification_text(
        payload, language, include_message_text=text_policy, markdown=True
    )
    pushed = json.loads(build_push_payload(payload, language))
    return Rendered(
        title=title,
        body_text=body_text,
        body_markdown=body_markdown,
        body_html=render_html_email(payload, language),
        tag=str(pushed.get("tag") or ""),
        url_hash=str(pushed.get("url_hash") or ""),
    )
