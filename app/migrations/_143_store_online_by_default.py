"""Migration 143: the Store is online by default (MSHL-456).

``app_settings.store_online`` was a consent, off until an administrator clicked. It is now
a setting that is on unless turned off. Every existing row goes to 1: a stored 0 cannot tell
"never asked" from "turned off", and 5.0.0, which brings the column, has no public users yet.
A default install with nothing installed still sends nothing (``app/plugins/store_egress.py``).

The column keeps its ``DEFAULT 0`` (SQLite cannot change it), which no longer matters:
``app_settings`` holds the one row created before this migration runs. Idempotent.
"""

import aiosqlite


async def migrate(conn: aiosqlite.Connection) -> None:
    async with conn.execute("PRAGMA table_info(app_settings)") as cursor:
        columns = {row[1] for row in await cursor.fetchall()}
    if "store_online" in columns:
        await conn.execute("UPDATE app_settings SET store_online = 1")
    await conn.commit()
