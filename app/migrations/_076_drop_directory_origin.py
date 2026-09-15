import logging

import aiosqlite

logger = logging.getLogger(__name__)

COLUMNS = ("directory_enabled", "directory_url")


async def migrate(conn: aiosqlite.Connection) -> None:
    """Drop the manual CoreScope origin settings.

    The directory is Meshloom Community only: there is no operator-supplied
    origin left to enable or point at.
    """
    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    for column in COLUMNS:
        if column not in columns:
            continue
        try:
            await conn.execute(f"ALTER TABLE app_settings DROP COLUMN {column}")
            await conn.commit()
        except Exception as e:
            error_msg = str(e).lower()
            if "syntax error" in error_msg or "drop column" in error_msg:
                logger.debug("SQLite has no DROP COLUMN; %s stays in app_settings", column)
                await conn.commit()
            else:
                raise
