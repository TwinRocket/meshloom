"""Migration 132: per-channel digest mode (MSHL-136).

``notification_channels.digest_interval_s``: 0 (the default) is off; otherwise 300..86400 s, the
interval of the summary that replaces the per-message notifications of the channel. Nothing
changes for an existing channel. Idempotent: a database created from ``SCHEMA_TABLES`` already
has the column.
"""

import aiosqlite


async def migrate(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(notification_channels)")
    existing = {row[1] for row in await cursor.fetchall()}
    if not existing or "digest_interval_s" in existing:
        return
    await conn.execute(
        "ALTER TABLE notification_channels ADD COLUMN digest_interval_s INTEGER NOT NULL DEFAULT 0"
    )
