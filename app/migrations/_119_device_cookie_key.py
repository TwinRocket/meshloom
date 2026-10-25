"""Migration 119: ``auth_settings.device_cookie_key``.

The HMAC key of the known-device cookies (``app/auth/device_cookie.py``), so
they survive a restart. NULL until first use, when the repository fills it
(``AuthSettingsRepository.device_cookie_key``). Never in the JSON backup
(``auth_settings`` is excluded) and cleared in the SQLite download.
``meshloom-admin rotate-device-key`` replaces it.
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "auth_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return
    col_cursor = await conn.execute("PRAGMA table_info(auth_settings)")
    if "device_cookie_key" not in {row[1] for row in await col_cursor.fetchall()}:
        await conn.execute("ALTER TABLE auth_settings ADD COLUMN device_cookie_key TEXT")
        logger.info("Added auth_settings.device_cookie_key")
    await conn.commit()
