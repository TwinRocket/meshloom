import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """PRIV with a firmware hash is observer-reach eligible, flood or directed."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await tables_cursor.fetchall()}
    if "messages" not in existing_tables:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(messages)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "packet_hash" not in columns or "observer_reach_eligible" not in columns:
        await conn.commit()
        return

    await conn.execute(
        """
        UPDATE messages
        SET observer_reach_eligible = 1
        WHERE packet_hash IS NOT NULL
          AND IFNULL(observer_reach_eligible, 0) = 0
        """
    )
    logger.info("Backfilled observer_reach_eligible for messages that already have a packet_hash")
    await conn.commit()
