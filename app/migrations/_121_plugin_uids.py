"""Migration 121: ``plugin_uids``, the uid each plugin instance runs under at the
``setuid`` isolation level (MSHL-339).

One row per instance that ever started under ``setuid``: allocated once, kept
across restarts, released only after a purge as that uid. A row may outlive its
instance (a purge that failed): the uid stays taken until startup purges it.
Rules: ``app/plugins/isolation/setuid.py``, ``docs/dev/plugins-security.md``.
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)

# Kept textually identical to SCHEMA_TABLES in app/database.py:
# tests/test_migrations/test_migration_121.py compares the two schemas.
_STATEMENT = """CREATE TABLE IF NOT EXISTS plugin_uids (
    instance_id TEXT PRIMARY KEY,
    uid INTEGER NOT NULL UNIQUE CHECK (uid > 0),
    allocated_at INTEGER NOT NULL
)"""


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add ``plugin_uids``. ``IF NOT EXISTS`` because ``SCHEMA_TABLES`` already
    creates it on a fresh database. Nothing to backfill: the instances that ran
    under the shared ``meshloom-plugin`` uid get their own at their next start, and
    startup purges what the shared uid left in the temp directories."""
    await conn.execute(_STATEMENT)
    logger.info("Plugin uid table ready")
    await conn.commit()
