import logging

import aiosqlite

logger = logging.getLogger(__name__)

_COLUMNS = (
    ("enable_message_poll_fallback", "INTEGER DEFAULT 0"),
    ("force_channel_slot_reconfigure", "INTEGER DEFAULT 0"),
    ("load_with_autoevict", "INTEGER DEFAULT 0"),
    ("legacy_env_settings_imported", "INTEGER DEFAULT 0"),
)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Move the radio workarounds and the VAPID contact out of the environment.

    ``MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK``, ``MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE``
    and ``MESHCORE_LOAD_WITH_AUTOEVICT`` become settings edited in Settings → Radio →
    Advanced. ``legacy_env_settings_imported`` marks the one-shot import of those
    variables (and of ``MESHCORE_VAPID_SUBJECT`` into ``vapid_subject``), done by
    ``services/legacy_env_settings.py`` at the first start; after it the variables
    are never read again. The columns start at 0: the import, not the migration,
    copies a value still set in the environment.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    for name, definition in _COLUMNS:
        if name not in columns:
            await conn.execute(f"ALTER TABLE app_settings ADD COLUMN {name} {definition}")
            logger.info("Added %s setting", name)
    await conn.commit()
