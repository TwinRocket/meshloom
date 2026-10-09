import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Opt-in retention for undecrypted raw packets (0 = keep everything)."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "raw_packet_retention_days" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN raw_packet_retention_days INTEGER DEFAULT 0"
        )
        logger.info("Added raw_packet_retention_days setting")
    await conn.commit()
