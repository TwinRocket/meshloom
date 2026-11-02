"""Migration 139: keep the channels on the radio (MSHL-41).

``app_settings.keep_channels_on_radio`` (``INTEGER DEFAULT 0``): Settings → Radio
→ Channels on the radio. Off keeps the historical behaviour (the connect sync
empties every channel slot). On, the sync copies the slots into the database
without clearing them and on-demand sends use one reserved scratch slot
(``app/radio.py`` ``plan_channel_send_slot``). Added only when ``app_settings``
exists. Idempotent.
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "keep_channels_on_radio" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN keep_channels_on_radio INTEGER DEFAULT 0"
        )
        logger.info("Added keep_channels_on_radio setting")
    await conn.commit()
