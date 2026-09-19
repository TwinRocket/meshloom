import json
import logging

import aiosqlite

logger = logging.getLogger(__name__)

_DEFAULT_DESTINATIONS = (
    '{"email": {"host": "", "port": 587, "mode": "starttls", "user": "", '
    '"password": "", "from": "", "to": ""}, '
    '"webhook": {"url": "", "hmac_secret": ""}}'
)


async def migrate(conn: aiosqlite.Connection) -> None:
    """v2 telemetry alert rules, notification destinations, tracked alerting=true."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}
    if "app_settings" not in tables:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}

    if "notification_destinations" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN notification_destinations "
            f"TEXT DEFAULT '{_DEFAULT_DESTINATIONS}'"
        )
        logger.info("Added app_settings.notification_destinations")

    from app.models import TelemetryAlertRuleOverride
    from app.telemetry_alerts import coerce_telemetry_alert_rules, stored_rules_dict

    row_cursor = await conn.execute("SELECT * FROM app_settings WHERE id = 1")
    row = await row_cursor.fetchone()
    if row is None:
        await conn.commit()
        return

    keys = set(row.keys())
    rules = coerce_telemetry_alert_rules(
        row["telemetry_alert_rules"] if "telemetry_alert_rules" in keys else None
    )

    push_on = True
    raw_push = row["push_defaults"] if "push_defaults" in keys else None
    if raw_push:
        try:
            loaded = json.loads(raw_push) if isinstance(raw_push, str) else raw_push
            if isinstance(loaded, dict) and "telemetry_alert" in loaded:
                push_on = bool(loaded["telemetry_alert"])
        except (TypeError, ValueError, json.JSONDecodeError):
            push_on = True
    rules.channels.push = push_on

    tracked: list[str] = []
    for col in ("tracked_telemetry_repeaters", "tracked_telemetry_contacts"):
        raw = row[col] if col in keys else None
        if not raw:
            continue
        try:
            loaded = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(loaded, list):
            tracked.extend(str(key).lower() for key in loaded if key)

    overrides = dict(rules.overrides or {})
    for key in tracked:
        existing = overrides.get(key)
        if existing is None:
            overrides[key] = TelemetryAlertRuleOverride(alerting=True)
        elif existing.alerting is None:
            overrides[key] = existing.model_copy(update={"alerting": True})
    rules.overrides = overrides

    payload = json.dumps(stored_rules_dict(rules))
    if "telemetry_alert_rules" in columns:
        await conn.execute(
            "UPDATE app_settings SET telemetry_alert_rules = ? WHERE id = 1",
            (payload,),
        )
        logger.info("Migrated telemetry_alert_rules to v2 shape")
    await conn.commit()
