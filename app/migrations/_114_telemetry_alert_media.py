"""Migration 114: keep the delivery of installs whose telemetry alert media lived only in
``telemetry_alert_rules.channels``.

4.18.x read the media of a telemetry alert from ``push_defaults.telemetry_alert`` and, when
that key was missing (an install that never saved push preferences), from
``telemetry_alert_rules.channels``. Migration 113 imported the rules from the raw
``push_defaults`` only, so such an install would have got ``telemetry.alert = [builtin:push]``.
This fills the missing key from the old field and sets the legacy media of ``telemetry.alert``
in the rules, for the channels that exist. Idempotent: it does nothing once the key is set.
"""

import json
import logging
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

MEDIUM_CHANNEL = {
    "push": "builtin:push",
    "email": "legacy:email",
    "webhook": "legacy:webhook",
    "apprise": "legacy:apprise",
}


def _object(raw: object) -> dict[str, Any]:
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return dict(raw) if isinstance(raw, dict) else {}


async def migrate(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await cursor.fetchall()}
    if not {"push_defaults", "telemetry_alert_rules"} <= columns:
        return
    has_rules = "notification_rules" in columns
    cursor = await conn.execute(
        "SELECT push_defaults, telemetry_alert_rules"
        + (", notification_rules" if has_rules else "")
        + " FROM app_settings WHERE id = 1"
    )
    row = await cursor.fetchone()
    if row is None:
        return
    defaults = _object(row[0])
    if "telemetry_alert" in defaults:
        return
    stored = _object(row[1]).get("channels")
    if not isinstance(stored, dict):
        return
    flags = {
        "push": bool(stored.get("push", True)),
        "email": bool(stored.get("email", False)),
        "webhook": bool(stored.get("webhook", False)),
        "apprise": bool(stored.get("apprise", False)),
    }
    defaults["telemetry_alert"] = flags
    await conn.execute(
        "UPDATE app_settings SET push_defaults = ? WHERE id = 1", (json.dumps(defaults),)
    )

    rules = _object(row[2]) if has_rules else {}
    events = rules.get("events")
    if isinstance(events, dict):
        cursor = await conn.execute("SELECT id FROM notification_channels")
        existing = {r[0] for r in await cursor.fetchall()}
        entry = events.setdefault("telemetry.alert", {})
        kept = [c for c in entry.get("channels") or [] if c not in MEDIUM_CHANNEL.values()]
        built = [cid for m, cid in MEDIUM_CHANNEL.items() if flags[m] and cid in existing]
        entry["channels"] = [*built, *kept]
        entry.setdefault("always_deliver", False)
        await conn.execute(
            "UPDATE app_settings SET notification_rules = ? WHERE id = 1",
            (json.dumps(rules, sort_keys=True),),
        )
    await conn.commit()
    logger.info("telemetry alert media taken from telemetry_alert_rules.channels")
