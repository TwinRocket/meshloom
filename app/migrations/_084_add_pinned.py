import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add a pinned flag to contacts and channels so conversations can stay at the top."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await tables_cursor.fetchall()}
    for table in ("contacts", "channels"):
        if table not in existing_tables:
            continue
        col_cursor = await conn.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in await col_cursor.fetchall()}
        if "pinned" not in columns:
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN pinned INTEGER DEFAULT 0")
            logger.info("Added pinned column to %s", table)
    await conn.commit()
