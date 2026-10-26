"""Generate (or check) the JSON Schema of the webhook body v2, and print its examples.

``docs/user/en/deep/notifications-webhook.schema.json`` is ``webhook_v2.schema_text()``;
``tests/test_webhook_v2.py`` fails when it drifts from the models (MSHL-137).

    uv run python scripts/dev/gen_webhook_schema.py [--check]
    uv run python scripts/dev/gen_webhook_schema.py --examples en|fr

``--examples`` prints one body per v2 event, built by the real code from sample payloads,
as the doc pages ``docs/user/{en,fr}/deep/notifications-webhook.md`` show them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "docs" / "user" / "en" / "deep" / "notifications-webhook.schema.json"

PUBKEY = "ab12cd34ef56" + "0123456789abcdef" * 3 + "0123456789ab"
NOW = 1767225600  # 2026-01-01T00:00:00Z

# One sample payload (the dispatcher's legacy dict) per v2 event.
SAMPLES: dict[str, dict[str, Any]] = {
    "message.dm": {
        "type": "PRIV",
        "conversation_key": PUBKEY,
        "sender_name": "Alice",
        "text": "On se retrouve au relais ?",
        "sender_timestamp": NOW,
    },
    "message.channel": {
        "type": "CHAN",
        "conversation_key": "8B3387E9C5CDEA6AC9E5EDBAA115CD72",
        "channel_name": "Public",
        "sender_name": "Bob",
        "text": "Bonjour",
        "sender_timestamp": NOW,
    },
    "message.mention": {
        "type": "CHAN",
        "conversation_key": "8B3387E9C5CDEA6AC9E5EDBAA115CD72",
        "channel_name": "Public",
        "sender_name": "Bob",
        "text": "@[Base] tu me reçois ?",
        "mention": True,
        "sender_timestamp": NOW,
    },
    "message.keyword": {
        "type": "CHAN",
        "conversation_key": "8B3387E9C5CDEA6AC9E5EDBAA115CD72",
        "channel_name": "#paris",
        "sender_name": "Bob",
        "text": "Bob: secours, panne de relais",
        "keyword": "secours",
        "keyword_id": "kw_1a2b3c4d",
        "sender_timestamp": NOW,
    },
    "contact.node_seen": {
        "event": "node_seen",
        "public_key": PUBKEY,
        "name": "Hilltop",
        "contact_type": 2,
        "silent_s": 0,
        "heard_at": NOW,
    },
    "contact.node_back": {
        "event": "node_back",
        "public_key": PUBKEY,
        "name": "Hilltop",
        "contact_type": 2,
        "silent_s": 2 * 86400 + 3600,
        "heard_at": NOW,
    },
    "contact.first_seen": {
        "event": "first_seen",
        "public_key": PUBKEY,
        "name": "Alice",
        "contact_type": 1,
    },
    "channel.found": {
        "event": "channel_found",
        "name": "#meshloom",
        "channel_key": "A1B2C3D4E5F60718293A4B5C6D7E8F90",
    },
    "telemetry.alert": {
        "event": "telemetry_alert",
        "rule_id": "battery",
        "public_key": PUBKEY,
        "name": "Relais Nord",
        "value": 3.46,
        "threshold": 3.5,
        "op": "lt",
    },
    "radio.offline": {
        "event": "radio_offline",
        "offline_s": 300,
        "outage_started_at": NOW,
        "connection": "TCP: 192.168.1.20:5000",
    },
    "radio.online": {
        "event": "radio_online",
        "downtime_s": 125,
        "outage_started_at": NOW,
        "connection": "TCP: 192.168.1.20:5000",
    },
    "app.started": {
        "event": "app_started",
        "version": "5.0.0",
        "connect_s": 12,
        "radio_connected": True,
        "started_at": NOW,
    },
    "app.update": {"event": "oss_update", "current": "5.0.0", "latest": "5.1.0"},
    "notify.destination_changed": {
        "event": "notify.destination_changed",
        "kinds": ["webhook"],
        "at": NOW,
    },
    "notify.channel_changed": {
        "event": "notify.channel_changed",
        "action": "created",
        "channel_type": "webhook",
        "name": "Node-RED",
    },
    "notify.channel_failed": {
        "event": "notify.channel_failed",
        "channel_id": "ntfy-1a2b3c",
        "name": "ntfy",
        "error": "auth_failed",
    },
    "notify.heartbeat": {
        "event": "notify.heartbeat",
        "instance": "Base Paris",
        "uptime_s": 93784,
        "version": "5.0.0",
    },
    "summary.grouped": {
        "event": "notifications_grouped",
        "count": 7,
        "dms": 2,
        "channels": {"Public": 5},
    },
    "summary.first_seen": {"event": "first_seen_grouped", "count": 4},
    "summary.digest": {
        "event": "quiet_digest",
        "scope": "quiet_hours",
        "count": 9,
        "conversations": 3,
        "items": [["Public", 6]],
        "dms": 3,
        "contacts": 2,
    },
}


def example_bodies(lang: str) -> dict[str, dict[str, Any]]:
    """One v2 body per event, as the transport would send it (text policy ``always``)."""
    from app.notifications import webhook_v2
    from app.notifications.events import TestData, TestEvent, event_from_payload

    instance = webhook_v2.Instance(name="Base", radio_public_key="cd" * 32)
    out: dict[str, dict[str, Any]] = {}
    for name in webhook_v2.BODIES:
        if name == "notify.test":
            event = TestEvent(
                event="notify.test",
                dedup_key="0" * 32,
                data=TestData(),
                event_id="0f1e2d3c-4b5a-4968-8776-655443322110",
                occurred_at=NOW,
            )
            payload = event.to_payload()
        else:
            payload = SAMPLES[name]
            event = event_from_payload(
                payload, event_id="0f1e2d3c-4b5a-4968-8776-655443322110", occurred_at=NOW
            )
        body = webhook_v2.build_body(
            payload, event, lang=lang, text_policy="always", instance=instance, now=NOW
        )
        assert body is not None, name
        out[name] = json.loads(webhook_v2.body_bytes(body))
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from app.notifications.webhook_v2 import schema_text

    if "--examples" in sys.argv:
        lang = sys.argv[sys.argv.index("--examples") + 1]
        for name, body in example_bodies(lang).items():
            print(f"### {name}\n\n```json\n{json.dumps(body, indent=2, ensure_ascii=False)}\n```\n")
        return 0
    text = schema_text()
    if "--check" in sys.argv:
        return 0 if SCHEMA.read_text(encoding="utf-8") == text else 1
    SCHEMA.write_text(text, encoding="utf-8")
    print(f"wrote {SCHEMA.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
