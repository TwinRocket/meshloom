"""Migration 123: ``user_notification_rules``, one account's notification choices (MSHL-321).

``app_settings.notification_rules`` stays the instance-wide document: it is now the admin
defaults every account follows. A row here holds what one account changed for its own Web Push
devices (``app/notifications/user_rules.py``): an event or a contact type forced on or off. An
account with no row, or an empty one, follows the defaults, so an upgrade changes nothing: no
row is written. ``ON DELETE CASCADE`` drops the choices with the account.
"""

import aiosqlite

# Kept textually identical to SCHEMA_TABLES in app/database.py:
# tests/test_migrations/test_migration_123.py compares the two schemas.
_STATEMENT = """CREATE TABLE IF NOT EXISTS user_notification_rules (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    rules TEXT NOT NULL DEFAULT '{}',
    updated_at INTEGER NOT NULL
)"""


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add the table. ``IF NOT EXISTS`` because ``SCHEMA_TABLES`` already creates it on a fresh
    database. Nothing to backfill: the current rules are the defaults as they stand."""
    await conn.execute(_STATEMENT)
