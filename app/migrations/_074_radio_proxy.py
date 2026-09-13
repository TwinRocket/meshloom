import logging

import aiosqlite

logger = logging.getLogger(__name__)

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("radio_proxy_enabled", "INTEGER DEFAULT 0"),
    ("radio_proxy_bind", "TEXT DEFAULT '0.0.0.0'"),
    ("radio_proxy_port", "INTEGER DEFAULT 5001"),
    ("radio_proxy_max_clients", "INTEGER DEFAULT 8"),
)


async def _ensure_repeater_pane_cache(conn: aiosqlite.Connection) -> None:
    """Create the pane-cache table if a previous 073 applied proxy columns instead.

    Origin numbered repeater pane cache as 073. A brief window numbered radio
    proxy as 073 too, so databases that only ran the proxy file sit at
    user_version=73 without ``repeater_pane_cache``. This is idempotent.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repeater_pane_cache (
            public_key TEXT NOT NULL,
            pane TEXT NOT NULL,
            data TEXT NOT NULL,
            fetched_at INTEGER NOT NULL,
            PRIMARY KEY (public_key, pane),
            FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_repeater_pane_cache_pk
            ON repeater_pane_cache (public_key)
        """
    )


async def migrate(conn: aiosqlite.Connection) -> None:
    """Persist radio-proxy listen settings on app_settings."""
    await _ensure_repeater_pane_cache(conn)

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
        logger.info("Added app_settings radio proxy columns: %s", ", ".join(added))
