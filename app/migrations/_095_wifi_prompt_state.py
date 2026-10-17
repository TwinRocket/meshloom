import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Remember, per radio, what Meshloom asked about its Wi-Fi credentials.

    JSON object keyed by the lowercase radio public key, one object per key
    with independent optional fields: ``dismissed_at`` (the user chose "don't
    ask again for this radio"; never erased), ``open_network_ssid`` /
    ``open_network_at`` (the user stored an open network, which reads the same
    as "nothing stored"), and ``store_started_at`` / ``store_target_ssid`` (a
    credentials store in progress, cleared once verified). Never a password.
    Written by ``AppSettingsRepository`` (``*_wifi_*`` helpers), read by
    ``services/radio_wifi.py``. Not part of ``AppSettings``.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "wifi_prompt_state" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN wifi_prompt_state TEXT DEFAULT '{}'"
        )
        logger.info("Added wifi_prompt_state setting")
    await conn.commit()
