"""Migration 131: per-channel heartbeat (MSHL-131).

``notification_channels.heartbeat_interval_s`` (0, the default, is off; otherwise
300..86400 s) and ``heartbeat_method`` (``GET`` or ``POST``, used by the optional ping
URL). The ping URL itself is a secret: it lives in the encrypted ``secrets`` column under
``heartbeat_url`` (``secret_store``), not in a column of its own. Nothing changes for an
existing channel. Idempotent: a database created from ``SCHEMA_TABLES`` already has the
columns.
"""

import aiosqlite

COLUMNS = (
    ("heartbeat_interval_s", "INTEGER NOT NULL DEFAULT 0"),
    ("heartbeat_method", "TEXT NOT NULL DEFAULT 'GET'"),
)


async def migrate(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(notification_channels)")
    existing = {row[1] for row in await cursor.fetchall()}
    if not existing:
        return
    for column, decl in COLUMNS:
        if column not in existing:
            await conn.execute(
                f"ALTER TABLE notification_channels ADD COLUMN {column} {decl}"  # noqa: S608
            )
