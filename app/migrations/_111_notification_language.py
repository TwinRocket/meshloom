import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add ``app_settings.notification_language`` (``auto``, ``fr`` or ``en``).

    The language of the e-mail and the Apprise text. ``auto``
    (the default, also for an existing row) keeps the previous behaviour: English
    only with a strict majority of English push subscriptions, a tie is French.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "notification_language" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN notification_language TEXT DEFAULT 'auto'"
        )
        logger.info("Added notification_language setting")
    await conn.commit()
