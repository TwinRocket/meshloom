"""Generate (or check) ``tests/fixtures/notifications_golden.json``.

The fixture records what every transport renders for each existing
notification event, in both languages, so the renderer can move without a
change of output. It was generated from the pre-refactor code (MSHL-107);
``tests/test_notifications_golden.py`` imports ``build_golden`` and compares.
Do not regenerate it to make a test pass: a diff is a behaviour change.

    uv run python scripts/dev/gen_notifications_golden.py [--check]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "notifications_golden.json"

PUBKEY = "ab12cd34ef56" + "0123456789abcdef" * 3 + "0123456789ab"
CHAN_KEY = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"
HASHTAG_KEY = "A1B2C3D4E5F60718293A4B5C6D7E8F90"

EVENTS: dict[str, dict] = {
    "message_dm": {
        "event": "message",
        "type": "PRIV",
        "conversation_key": PUBKEY,
        "sender_name": "Léa",
        "text": "Salut @everyone <@123> *gras* [lien](http://x)",
    },
    "message_dm_anonymous": {
        "type": "PRIV",
        "conversation_key": PUBKEY,
        "text": "hello",
    },
    "message_channel_public": {
        "event": "message",
        "type": "CHAN",
        "conversation_key": CHAN_KEY,
        "channel_name": "Public",
        "sender_name": "Bob",
        "text": "Hello @channel",
    },
    "message_channel_hashtag": {
        "event": "message",
        "type": "CHAN",
        "conversation_key": HASHTAG_KEY,
        "channel_name": "#test",
        "text": "plain",
    },
    "message_channel_private": {
        "event": "message",
        "type": "CHAN",
        "conversation_key": "00112233445566778899AABBCCDDEEFF",
        "channel_name": "Secret",
        "sender_name": "Eve",
        "text": "<!here> hidden_text",
    },
    "message_channel_unnamed": {
        "type": "CHAN",
        "conversation_key": "00112233445566778899AABBCCDDEEFF",
        "text": "x",
    },
    "first_seen_companion": {
        "event": "first_seen",
        "public_key": PUBKEY,
        "name": "Alice",
        "contact_type": 1,
    },
    "first_seen_repeater": {
        "event": "first_seen",
        "public_key": PUBKEY,
        "name": "Rpt <b>&",
        "contact_type": 2,
    },
    "first_seen_sensor_unnamed": {
        "event": "first_seen",
        "public_key": PUBKEY,
        "contact_type": 4,
    },
    "first_seen_empty": {"event": "first_seen"},
    "first_seen_grouped": {"event": "first_seen_grouped", "count": 4},
    "notifications_grouped": {"event": "notifications_grouped", "count": 7},
    "channel_found": {"event": "channel_found", "name": "#meshloom", "channel_key": CHAN_KEY},
    "channel_found_unnamed": {"event": "channel_found", "channel_key": CHAN_KEY},
    "oss_update": {"event": "oss_update", "current": "5.0.0", "latest": "5.1.0"},
    "destination_changed": {
        "event": "notify.destination_changed",
        "kinds": ["email", "webhook", "apprise"],
    },
    "radio_offline": {
        "event": "radio_offline",
        "offline_s": 3725,
        "connection": "Serial: /dev/ttyUSB0",
        "urgent": True,
    },
    "radio_online": {
        "event": "radio_online",
        "downtime_s": 125,
        "connection": "TCP: host:5000",
    },
    "app_started": {
        "event": "app_started",
        "version": "5.0.0",
        "connect_s": 12,
        "radio_connected": True,
    },
    "app_started_waiting": {"event": "app_started", "radio_connected": False},
    "app_started_connected_no_time": {
        "event": "app_started",
        "version": "5.0.0",
        "radio_connected": True,
    },
    "telemetry_battery": {
        "event": "telemetry_alert",
        "rule_id": "battery",
        "public_key": PUBKEY,
        "name": "Node1",
        "value": 3.46,
        "threshold": 3.5,
        "op": "lt",
    },
    "telemetry_noise": {
        "event": "telemetry_alert",
        "rule_id": "noise",
        "public_key": PUBKEY,
        "value": -90,
        "threshold": -100,
        "op": "gt",
    },
    "telemetry_rssi": {
        "event": "telemetry_alert",
        "rule_id": "rssi",
        "name": "N",
        "value": -120,
        "threshold": -110,
        "op": "lt",
    },
    "telemetry_snr": {
        "event": "telemetry_alert",
        "rule_id": "snr",
        "name": "N",
        "value": -5,
        "threshold": -2,
        "op": "lt",
    },
    "telemetry_tx_queue": {
        "event": "telemetry_alert",
        "rule_id": "tx_queue",
        "name": "N",
        "value": 12,
        "threshold": 8,
        "op": "gt",
    },
    "telemetry_silence": {
        "event": "telemetry_alert",
        "rule_id": "silence",
        "name": "N",
        "value": 3,
        "threshold": 3,
        "op": "gt",
    },
    "telemetry_gps_lost": {
        "event": "telemetry_alert",
        "rule_id": "gps_lost",
        "public_key": PUBKEY,
    },
    "telemetry_lpp_temperature": {
        "event": "telemetry_alert",
        "rule_id": "lpp:temperature:1",
        "name": "N",
        "value": 41.256,
        "threshold": 40,
        "op": "gt",
    },
    "telemetry_plain_humidity": {
        "event": "telemetry_alert",
        "rule_id": "humidity",
        "name": "N",
        "value": 10,
        "threshold": 20,
        "op": "lt",
    },
    "telemetry_unknown_rule": {
        "event": "telemetry_alert",
        "rule_id": "custom",
        "name": "N",
        "value": 1,
        "threshold": 2,
        "op": "gt",
    },
    "telemetry_unknown_no_threshold": {
        "event": "telemetry_alert",
        "rule_id": "custom",
        "name": "N",
        "value": "x",
    },
    "telemetry_test": {
        "event": "telemetry_alert",
        "rule_id": "test",
        "name": "Test node",
        "value": 3.46,
        "threshold": 3.5,
        "op": "lt",
        "metric": "battery",
        "unit": "V",
    },
    "unknown_event": {"event": "something_else", "name": "x"},
}

POLICIES = ("always", "public_only", "never")


def build_golden() -> dict:
    from app.email_template import (
        email_subject,
        enrich_notification_payload,
        notification_email_lines,
        render_html_email,
        render_plain_email,
    )
    from app.push.manager import _build_payload, event_notification_text

    out: dict[str, dict] = {}
    for name, data in EVENTS.items():
        per_lang: dict[str, dict] = {}
        for lang in ("fr", "en"):
            intro, lines = notification_email_lines(data, lang)
            entry = {
                "push": json.loads(_build_payload(data, lang)),
                "push_raw": _build_payload(data, lang),
                "email_subject": email_subject(data, lang),
                "email_subject_urgent": email_subject({**data, "urgent": True}, lang),
                "email_lines": [intro, lines],
                "email_plain": render_plain_email(data, lang),
                "email_html": render_html_email(data, lang),
                "webhook_body": json.dumps(
                    enrich_notification_payload(data), separators=(",", ":"), sort_keys=True
                ),
                "text": {},
            }
            for policy in POLICIES:
                for markdown in (False, True):
                    entry["text"][f"{policy}|{'md' if markdown else 'plain'}"] = list(
                        event_notification_text(
                            data, lang, include_message_text=policy, markdown=markdown
                        )
                    )
            per_lang[lang] = entry
        # An unsupported language falls back to French everywhere.
        per_lang["xx"] = {
            "push_raw": _build_payload(data, "xx"),
            "text": list(event_notification_text(data, "xx")),
        }
        out[name] = per_lang
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT))
    golden = build_golden()
    text = json.dumps(golden, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        return 0 if FIXTURE.read_text(encoding="utf-8") == text else 1
    FIXTURE.write_text(text, encoding="utf-8")
    print(f"wrote {FIXTURE.relative_to(ROOT)} ({len(golden)} events)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
