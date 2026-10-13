import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Remember an opt-out that Community did not confirm.

    JSON object ``{"pubkey", "reason", "requested_at", "iata"}`` written just
    before the ``POST /v1/me/opt-out`` of an opt-out and emptied once Community
    confirms it (or a re-join makes it moot). Empty means nothing is pending.
    Read by ``services/meshloom_community.opt_out_pending()``; the UI then shows
    "Deletion not confirmed" with a manual retry (no automatic retry).
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "community_opt_out_pending" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN community_opt_out_pending TEXT DEFAULT ''"
        )
        logger.info("Added community_opt_out_pending setting")
    await conn.commit()
