"""Migration 130: per-channel message templates (MSHL-127).

``notification_channels.template_dm`` and ``template_channel``: the body of a direct
message and of a channel message for the text-body types (Apprise and its presets, ntfy,
Discord), written with the fanout Apprise variables. Empty (the default) means the
renderer's own body, so nothing changes for an existing channel. They are not secrets:
they stay outside the encrypted set of ``secret_store``. Idempotent: a database created
from ``SCHEMA_TABLES`` already has the columns.
"""

import aiosqlite

COLUMNS = ("template_dm", "template_channel")


async def migrate(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(notification_channels)")
    existing = {row[1] for row in await cursor.fetchall()}
    if not existing:
        return
    for column in COLUMNS:
        if column not in existing:
            await conn.execute(
                f"ALTER TABLE notification_channels ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"  # noqa: S608
            )
