import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Remember the last retained Community MQTT status topic across restarts.

    JSON object (IATA, broker, audience, pubkey), empty when none. Written by
    ``services/community_status_clear.py`` when the Stats publisher makes its
    ``online`` status retained. At the next start, if Community is off or the
    IATA or broker changed while the app was stopped (env or database), that
    topic is handed to the bounded status clear instead of keeping a retained
    ``offline`` forever.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "community_status_last" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN community_status_last TEXT DEFAULT ''"
        )
        logger.info("Added community_status_last setting")
    await conn.commit()
