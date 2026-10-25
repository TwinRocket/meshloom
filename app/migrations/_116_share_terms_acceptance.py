"""Migration 116: server-side record of the sharing terms acceptance (MSHL-292).

``share_terms_version`` is the version of the share terms the owner accepted in the consent
dialog (0 = never) and ``share_terms_accepted_at`` the Unix time of that acceptance (0 =
never). An instance that enabled sharing before this migration has no recorded acceptance, so
it is asked again (sharing stays off until it accepts). Both columns are consent state and
never travel in a backup.
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)

_COLUMNS = (
    ("share_terms_version", "INTEGER DEFAULT 0"),
    ("share_terms_accepted_at", "INTEGER DEFAULT 0"),
)


async def migrate(conn: aiosqlite.Connection) -> None:
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    for name, ddl in _COLUMNS:
        if name not in columns:
            await conn.execute(f"ALTER TABLE app_settings ADD COLUMN {name} {ddl}")
            logger.info("Added %s setting", name)
    await conn.commit()
