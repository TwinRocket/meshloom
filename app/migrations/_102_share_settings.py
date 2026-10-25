import logging

import aiosqlite

logger = logging.getLogger(__name__)

# (column, DDL). Defaults are off / auto / private, also for an existing row.
_COLUMNS = (
    ("share_enabled", "INTEGER DEFAULT 0"),
    ("share_shorten_mode", "TEXT DEFAULT 'auto'"),
    ("share_preview_policy", "TEXT DEFAULT 'private'"),
    ("share_instance_seed", "TEXT DEFAULT ''"),
)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add the link and file sharing settings to ``app_settings``.

    ``share_enabled`` is the instance-level consent (off), ``share_shorten_mode``
    (``auto``, ``always``, ``never``) and ``share_preview_policy`` (``always``,
    ``private``, ``never``) are the two behaviours. ``share_instance_seed`` is the
    instance identity seed: generated on first enablement, never in ``AppSettings``,
    never exported by a backup.
    """
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
