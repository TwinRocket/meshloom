"""Channel membership (adopted/pending) and the refused-channel denylist."""

import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}

    if "channels" in tables:
        col_cursor = await conn.execute("PRAGMA table_info(channels)")
        columns = {row[1] for row in await col_cursor.fetchall()}
        if "membership" not in columns:
            await conn.execute(
                "ALTER TABLE channels ADD COLUMN membership TEXT NOT NULL DEFAULT 'adopted'"
            )
            logger.info("Added channels.membership")

    if "app_settings" in tables:
        col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
        columns = {row[1] for row in await col_cursor.fetchall()}
        if "rejected_channels" not in columns:
            await conn.execute(
                "ALTER TABLE app_settings ADD COLUMN rejected_channels TEXT DEFAULT '[]'"
            )
            logger.info("Added app_settings.rejected_channels")

    await conn.commit()
