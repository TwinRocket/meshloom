"""Migration 146: drop ``auth_settings.setup_code_hash``.

The installation code is gone: from the local network the administrator is created
directly, and from elsewhere setup is refused (``app/auth/first_run.py``). On SQLite
without ``DROP COLUMN`` (before 3.35) the unused column stays.

Idempotent: a second run changes nothing.
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    async with conn.execute("PRAGMA table_info(auth_settings)") as cursor:
        columns = {row[1] for row in await cursor.fetchall()}
    if "setup_code_hash" in columns:
        try:
            await conn.execute("ALTER TABLE auth_settings DROP COLUMN setup_code_hash")
        except aiosqlite.OperationalError as exc:
            if "syntax error" not in str(exc).lower() and "drop column" not in str(exc).lower():
                raise
            logger.debug("SQLite has no DROP COLUMN; setup_code_hash stays unused")
    await conn.commit()
