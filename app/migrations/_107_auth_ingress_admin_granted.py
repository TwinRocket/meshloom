import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add ``auth_settings.ingress_admin_granted``: set once the first Home
    Assistant identity was made ``admin``, never cleared, so a deleted ingress
    identity comes back with ``ingress_default_role`` instead of ``admin``
    (``app/auth/providers/ingress.py``). Backfilled to 1 when an ingress
    identity already exists (the first one got ``admin`` then).
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}
    if "auth_settings" not in tables:
        await conn.commit()
        return
    col_cursor = await conn.execute("PRAGMA table_info(auth_settings)")
    if "ingress_admin_granted" not in {row[1] for row in await col_cursor.fetchall()}:
        await conn.execute(
            "ALTER TABLE auth_settings ADD COLUMN ingress_admin_granted INTEGER NOT NULL DEFAULT 0"
        )
        logger.info("Added auth_settings.ingress_admin_granted")
    if "users" in tables:
        await conn.execute(
            "UPDATE auth_settings SET ingress_admin_granted = 1"
            " WHERE EXISTS (SELECT 1 FROM users WHERE provider = 'ingress')"
        )
    await conn.commit()
