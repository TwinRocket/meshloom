import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add the radio offline watch settings (``app/notifications/radio_watch.py``).

    ``radio_offline_grace_s`` (Settings → Notifications) is how long the radio may
    stay unreachable before ``radio_offline`` is sent; 0 means at once. It starts
    at 60 s. ``radio_outage_started_at`` is internal: the Unix time a *notified*
    outage began, kept so a restart during the outage still reports the right
    downtime with ``radio_online``. NULL means no notified outage.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "radio_offline_grace_s" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN radio_offline_grace_s INTEGER DEFAULT 60"
        )
        logger.info("Added radio_offline_grace_s setting")
    if "radio_outage_started_at" not in columns:
        await conn.execute("ALTER TABLE app_settings ADD COLUMN radio_outage_started_at REAL")
        logger.info("Added radio_outage_started_at state")
    await conn.commit()
