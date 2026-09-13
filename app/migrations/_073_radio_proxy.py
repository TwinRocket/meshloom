import logging

import aiosqlite

logger = logging.getLogger(__name__)

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("radio_proxy_enabled", "INTEGER DEFAULT 0"),
    ("radio_proxy_bind", "TEXT DEFAULT '0.0.0.0'"),
    ("radio_proxy_port", "INTEGER DEFAULT 5001"),
    ("radio_proxy_max_clients", "INTEGER DEFAULT 8"),
)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Persist radio-proxy listen settings on app_settings."""
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}
    if "app_settings" not in tables:
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
        logger.info("Added app_settings radio proxy columns: %s", ", ".join(added))
