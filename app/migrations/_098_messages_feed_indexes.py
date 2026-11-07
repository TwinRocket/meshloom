import logging

import aiosqlite

logger = logging.getLogger(__name__)

FEED_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_messages_feed_type ON messages(type, received_at)",
    "CREATE INDEX IF NOT EXISTS idx_messages_feed_outgoing ON messages(outgoing, received_at)",
    "CREATE INDEX IF NOT EXISTS idx_messages_feed_type_outgoing "
    "ON messages(type, outgoing, received_at)",
)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Indexes for the global timeline (``GET /api/messages`` without a conversation).

    The listing orders by ``received_at DESC, id DESC``. ``id`` is the rowid, so every
    index ends with it implicitly: walking one backwards yields the order with no temp
    B-tree. Each form of the type / outgoing filters gets an index that starts with its
    equality columns (the existing ones put ``conversation_key`` in between).
    """
    cursor = await conn.execute("PRAGMA table_info(messages)")
    columns = {row[1] for row in await cursor.fetchall()}
    if {"type", "outgoing", "received_at"} <= columns:
        for statement in FEED_INDEXES:
            await conn.execute(statement)
        logger.info("Created message feed indexes")
    await conn.commit()
