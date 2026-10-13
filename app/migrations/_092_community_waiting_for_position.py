import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Remember across restarts whether Community waits for the radio's position.

    1 when the last radio identity Meshloom accepted had no position Community
    may publish, 0 otherwise (and by default: nothing known yet). Read at
    startup by ``services/meshloom_community.load_position_state()``, so a radio
    known to be waiting stays waiting before it reconnects (no unauthenticated
    Community call, no packet).
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "community_waiting_for_position" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN community_waiting_for_position INTEGER DEFAULT 0"
        )
        logger.info("Added community_waiting_for_position setting")
    await conn.commit()
