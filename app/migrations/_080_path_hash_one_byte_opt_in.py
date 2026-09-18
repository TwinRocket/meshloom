import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Remember an explicit operator choice to keep 1-byte path hashes.

    Meshloom applies 2-byte path hashing on connect unless this flag is set.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}
    if "app_settings" not in tables:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "path_hash_one_byte_opt_in" in columns:
        await conn.commit()
        return

    await conn.execute(
        "ALTER TABLE app_settings ADD COLUMN path_hash_one_byte_opt_in INTEGER NOT NULL DEFAULT 0"
    )
    await conn.commit()
    logger.info("Added app_settings.path_hash_one_byte_opt_in")
