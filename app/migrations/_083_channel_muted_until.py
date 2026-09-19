import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add muted_until so channel mute can expire."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}
    if "channels" not in tables:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(channels)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "muted_until" not in columns:
        await conn.execute("ALTER TABLE channels ADD COLUMN muted_until INTEGER")
        logger.info("Added channels.muted_until")

    await conn.commit()
