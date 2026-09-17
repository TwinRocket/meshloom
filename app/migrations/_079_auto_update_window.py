import logging

import aiosqlite

logger = logging.getLogger(__name__)

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("auto_update_window_start", "TEXT NOT NULL DEFAULT '00:00'"),
    ("auto_update_window_end", "TEXT NOT NULL DEFAULT '00:00'"),
    ("auto_update_weekdays", "TEXT NOT NULL DEFAULT '[0,1,2,3,4,5,6]'"),
    ("last_notified_update_version", "TEXT"),
)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Persist the OSS auto-update window and last Web-Pushed catalogue version.

    Window fields are patched via PATCH /api/updates/settings, never
    PATCH /api/settings. ``last_notified_update_version`` is notify-once state.
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
        logger.info("Added app_settings auto-update window columns: %s", ", ".join(added))
