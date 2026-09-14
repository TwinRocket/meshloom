import logging

import aiosqlite

logger = logging.getLogger(__name__)

_COLUMNS: tuple[tuple[str, str], ...] = (
    # A JSON object rather than a column per preference: these are chrome, they
    # change shape with the interface, and a migration per checkbox is a poor
    # trade for settings nothing queries or joins on.
    ("ui_preferences", "TEXT DEFAULT '{}'"),
)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Persist interface preferences on app_settings.

    These lived in each browser's localStorage, which meant setting them again on
    every device reaching the same instance. Migration 009 already recorded the
    intention to move them here — it added ``preferences_migrated`` and stopped.
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
        logger.info("Added app_settings interface preference columns: %s", ", ".join(added))
