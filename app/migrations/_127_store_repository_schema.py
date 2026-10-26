"""Migration 127: ``store_repositories.config_schema`` (MSHL-197).

The config schema of the cached release (``config.schema.json`` read from the checked
archive, JSON; NULL when the release has none or was cached before this migration), so
``GET /api/store/updates`` can show which config fields an update adds without a
download. ``app/plugins/custom_repos.py`` writes it with the release.
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "store_repositories" not in {row[0] for row in await tables_cursor.fetchall()}:
        return
    col_cursor = await conn.execute("PRAGMA table_info(store_repositories)")
    if "config_schema" not in {row[1] for row in await col_cursor.fetchall()}:
        await conn.execute("ALTER TABLE store_repositories ADD COLUMN config_schema TEXT")
        logger.info("Added store_repositories.config_schema")
