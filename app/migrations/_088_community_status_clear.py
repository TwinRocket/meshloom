import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Persist the pending clear of a retained Community MQTT status.

    JSON list, empty when nothing is pending. Written by
    ``services/community_status_clear.py`` when an opt-out (or an IATA or
    broker change) could not clear the retained status on the broker, so the
    single bounded retry survives a restart.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "community_status_clear" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN community_status_clear TEXT DEFAULT '[]'"
        )
        logger.info("Added community_status_clear setting")
    await conn.commit()
