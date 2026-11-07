"""Migration 117: ``sessions.remember``, the "remember me" choice of a login.

1 (the default, so every existing session keeps its long, cookie-with-Max-Age
lifetime) or 0, a browser-session login: cookie without ``Max-Age``, and a short
server-side sliding lifetime (``app/auth/sessions.py``). Idempotent.
"""

import aiosqlite


async def migrate(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(sessions)")
    columns = {row[1] for row in await cursor.fetchall()}
    if columns and "remember" not in columns:
        await conn.execute("ALTER TABLE sessions ADD COLUMN remember INTEGER NOT NULL DEFAULT 1")
