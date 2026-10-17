import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add ``push_allow_private_endpoints`` (Settings → Notifications), off.

    The egress guard (``app/net/egress_guard.py``) refuses a Web Push endpoint
    on a private network (LAN, loopback, CGNAT...) unless this setting is on: a
    self-hosted UnifiedPush distributor needs it. Browser push services are all
    public, so existing installs start off too.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "push_allow_private_endpoints" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN push_allow_private_endpoints INTEGER DEFAULT 0"
        )
        logger.info("Added push_allow_private_endpoints setting")
    await conn.commit()
