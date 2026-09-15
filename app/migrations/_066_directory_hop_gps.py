import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Store optional directory GPS/pubkey on hop-cache rows for path maps."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}
    if "directory_hop_cache" not in tables:
        return

    col_cursor = await conn.execute("PRAGMA table_info(directory_hop_cache)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "public_key" not in columns:
        await conn.execute("ALTER TABLE directory_hop_cache ADD COLUMN public_key TEXT")
    if "lat" not in columns:
        await conn.execute("ALTER TABLE directory_hop_cache ADD COLUMN lat REAL")
    if "lon" not in columns:
        await conn.execute("ALTER TABLE directory_hop_cache ADD COLUMN lon REAL")
    await conn.commit()
    logger.info("Added directory_hop_cache GPS columns")
