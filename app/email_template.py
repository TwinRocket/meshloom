"""HTML notification emails (instance language, Meshloom mark, richer body)."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Any, Literal

LOGO_URL = "https://meshloom.app/meshloom-mark.svg"
SITE_URL = "https://meshloom.app"

AppLanguage = Literal["fr", "en"]

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
        text = str(payload.get("text") or "").strip()
        lines = []
        if sender:
            lines.append(f"{copy['sender']} : {sender}")
        if channel:
            lines.append(f"{copy['channel']} : {channel}")
        if text:
            lines.append(text)
        return copy["intro_message"], lines
    from app.push.manager import event_notification_text

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
                    <img src="{escape(LOGO_URL, quote=True)}" width="40" height="32" alt="Meshloom" style="display:block;border:0;width:40px;height:auto;" />
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
              <a href="{SITE_URL}" style="color:#3ee8ff;text-decoration:none;">{escape(copy["footer"])}</a>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def email_subject(payload: Mapping[str, Any], lang: AppLanguage) -> str:
    from app.push.manager import event_notification_text

    title, _ = event_notification_text(dict(payload), lang)
    return title or "Meshloom"


async def resolve_instance_language() -> AppLanguage:
    """Best instance-language proxy: majority of subscribed browsers, else French."""
    try:
        from app.repository.push_subscriptions import PushSubscriptionRepository

        subs = await PushSubscriptionRepository.get_all()
    except Exception:
        return "fr"
    english = sum(1 for sub in subs if normalize_email_language(sub.get("language")) == "en")
    french = sum(1 for sub in subs if normalize_email_language(sub.get("language")) == "fr")
    return "en" if english > french else "fr"
