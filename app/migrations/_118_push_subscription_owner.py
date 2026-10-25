"""Migration 118: a Web Push subscription belongs to an account (``user_id``).

Before 5.0.0 a subscription had no owner: any account holding ``send`` listed, renamed,
tested and deleted every device (MSHL-290). The column references ``users(id)`` with
``ON DELETE CASCADE``: deleting an account deletes its devices. Existing subscriptions go to
the first administrator (lowest id, an enabled one first). With no administrator yet (an
upgrade from 4.18.x before the first account is created) they stay unowned and
``PushSubscriptionRepository`` hands them to the first administrator once one exists.
Idempotent: a database created from ``SCHEMA_TABLES`` already has the column.
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)

FIRST_ADMIN_SQL = "SELECT id FROM users WHERE role = 'admin' ORDER BY disabled, id LIMIT 1"


async def migrate(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(push_subscriptions)")
    columns = {row[1] for row in await cursor.fetchall()}
    if not columns:
        return
    if "user_id" not in columns:
        # SQLite accepts a REFERENCES clause on ADD COLUMN when the default is NULL.
        await conn.execute(
            "ALTER TABLE push_subscriptions ADD COLUMN user_id INTEGER "
            "REFERENCES users(id) ON DELETE CASCADE"
        )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user ON push_subscriptions(user_id)"
    )
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
    )
    if await cursor.fetchone() is None:
        return
    cursor = await conn.execute(FIRST_ADMIN_SQL)
    row = await cursor.fetchone()
    if row is None:
        return
    cursor = await conn.execute(
        "UPDATE push_subscriptions SET user_id = ? WHERE user_id IS NULL", (row[0],)
    )
    if cursor.rowcount:
        logger.info("%d push subscription(s) given to the first administrator", cursor.rowcount)
