import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add ``auth_allowed_origins``: the cross-origin sites allowed by CORS.

    A JSON list of origins (``scheme://host[:port]``), empty by default: no other
    site gets CORS headers, where the API used to reflect every ``Origin`` with
    credentials. Read by ``app/cors.py``.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "auth_allowed_origins" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN auth_allowed_origins TEXT DEFAULT '[]'"
        )
        logger.info("Added auth_allowed_origins setting")
    await conn.commit()
