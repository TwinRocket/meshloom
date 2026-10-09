import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Partial indexes for contact/message reconciliation.

    ``backfill_channel_sender_key`` and ``claim_prefix_messages`` run whenever a
    contact identity is (re)learned. Without these they scan every CHAN / PRIV
    row; both indexes only hold the few rows still awaiting reconciliation.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in await tables_cursor.fetchall()}
    if "messages" not in existing_tables:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(messages)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if {"type", "sender_name", "sender_key"} <= columns:
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_chan_unattributed_sender
            ON messages(sender_name) WHERE type = 'CHAN' AND sender_key IS NULL
            """
        )
    if {"type", "conversation_key"} <= columns:
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_priv_prefix_key
            ON messages(conversation_key)
            WHERE type = 'PRIV' AND length(conversation_key) < 64
            """
        )
    logger.info("Created partial indexes for contact/message reconciliation")
    await conn.commit()
