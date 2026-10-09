import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Partial index for channel sender backfill.

    ``backfill_channel_sender_key`` runs whenever a contact identity is
    (re)learned. Without this index it scans every CHAN row; the index only
    holds the rows still awaiting attribution.
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
    logger.info("Created partial index for channel sender backfill")
    await conn.commit()
