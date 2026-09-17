import logging

import aiosqlite

logger = logging.getLogger(__name__)

_COLUMNS: tuple[tuple[str, str], ...] = (("auto_update", "INTEGER NOT NULL DEFAULT 0"),)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Persist in-app auto-update on app_settings.

    This is the apply-path toggle (PATCH /api/updates/settings), not a general
    settings field and never an OS apt upgrade.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}
    if "app_settings" not in tables:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    added: list[str] = []
    for name, definition in _COLUMNS:
        if name in columns:
            continue
        await conn.execute(f"ALTER TABLE app_settings ADD COLUMN {name} {definition}")
        added.append(name)
    await conn.commit()
    if added:
        logger.info("Added app_settings auto-update columns: %s", ", ".join(added))
