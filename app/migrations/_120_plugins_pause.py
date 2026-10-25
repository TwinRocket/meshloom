"""Migration 120: the global plugin pause (``app_settings.plugins_paused*``).

The incident switch of Settings > Extensions (MSHL-216): ``plugins_paused``
(0/1), ``plugins_paused_at`` (unix seconds) and ``plugins_paused_by`` (the
username of the admin who paused). Read at boot by ``app/plugins/pause.py``
so a pause survives a restart. Not in ``AppSettings``: only
``PUT /api/plugins/pause`` writes them.
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)

_COLUMNS = (
    ("plugins_paused", "INTEGER DEFAULT 0"),
    ("plugins_paused_at", "INTEGER"),
    ("plugins_paused_by", "TEXT"),
)


async def migrate(conn: aiosqlite.Connection) -> None:
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return
    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    existing = {row[1] for row in await col_cursor.fetchall()}
    for name, ddl in _COLUMNS:
        if name not in existing:
            await conn.execute(f"ALTER TABLE app_settings ADD COLUMN {name} {ddl}")
            logger.info("Added app_settings.%s", name)
    await conn.commit()
