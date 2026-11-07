"""Migration 128: ``store_repositories.readme`` (MSHL-198).

The README files of the cached release (``{lang: text}`` as JSON, read from the checked
archive; NULL when the archive was not read or the release was cached before this
migration), so the Store page can show a plugin's README before its installation. The
text is data: the UI renders it as plain text only. ``app/plugins/custom_repos.py``
writes it with the release.
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "store_repositories" not in {row[0] for row in await tables_cursor.fetchall()}:
        return
    col_cursor = await conn.execute("PRAGMA table_info(store_repositories)")
    if "readme" not in {row[1] for row in await col_cursor.fetchall()}:
        await conn.execute("ALTER TABLE store_repositories ADD COLUMN readme TEXT")
        logger.info("Added store_repositories.readme")
