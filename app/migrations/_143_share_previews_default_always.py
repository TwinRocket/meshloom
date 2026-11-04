"""Migration 143: shared image previews default to ``always``.

The default was ``private``. SQLite cannot change a column default, and nothing tells
a default from a choice in the stored value, so the rule is: an instance that has
never turned sharing on made no choice, an instance with sharing on keeps its value.
Fresh databases go through this too (their row exists before the migrations run).

Idempotent: a second run changes nothing.
"""

import aiosqlite


async def migrate(conn: aiosqlite.Connection) -> None:
    # Absent from partial test databases, as in migration 102.
    cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await cursor.fetchall()}
    if {"share_enabled", "share_preview_policy"} <= columns:
        await conn.execute(
            "UPDATE app_settings SET share_preview_policy = 'always'"
            " WHERE share_preview_policy = 'private' AND share_enabled = 0"
        )
    await conn.commit()
