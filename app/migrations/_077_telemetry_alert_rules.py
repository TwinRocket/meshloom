import logging

import aiosqlite

logger = logging.getLogger(__name__)

_DEFAULT_RULES = (
    '{"battery_volts_min": 3.5, "noise_floor_max_dbm": -90, '
    '"misses_before_alert": 2, "overrides": {}}'
)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add telemetry alert rules JSON and persist per-node alert state."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}

    if "app_settings" in tables:
        col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
        columns = {row[1] for row in await col_cursor.fetchall()}
        if "telemetry_alert_rules" not in columns:
            await conn.execute(
                "ALTER TABLE app_settings ADD COLUMN telemetry_alert_rules "
                f"TEXT DEFAULT '{_DEFAULT_RULES}'"
            )
            logger.info("Added app_settings.telemetry_alert_rules")

    if "telemetry_alert_state" not in tables:
        await conn.execute(
            """
            CREATE TABLE telemetry_alert_state (
                public_key TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                consecutive_misses INTEGER NOT NULL DEFAULT 0,
                last_fired_at INTEGER,
                last_value REAL,
                PRIMARY KEY (public_key, rule_id),
                FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
            )
            """
        )
        logger.info("Created telemetry_alert_state")

    await conn.commit()
