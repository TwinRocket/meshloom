import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add the automatic clock wraparound workaround (Settings → Radio → Advanced).

    ``auto_clock_wraparound`` lets the post-connect clock sync fix a radio whose
    clock is still ahead after Meshloom rebooted it: set ``0xFFFFFFFF``, wait for
    the uint32 overflow to 0, then set the current time (``radio_sync.py``). It
    starts on (``DEFAULT 1``), for existing databases too: it only fires on a
    radio that already rejects every time update. It replaces the developer-only
    ``__CLOWNTOWN_DO_CLOCK_WRAPAROUND`` variable, which is no longer read
    (``services/legacy_env_settings.py`` warns about it).
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "auto_clock_wraparound" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN auto_clock_wraparound INTEGER DEFAULT 1"
        )
        logger.info("Added auto_clock_wraparound setting")
    await conn.commit()
