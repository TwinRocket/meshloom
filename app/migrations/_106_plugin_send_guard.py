import logging

import aiosqlite

logger = logging.getLogger(__name__)

# Kept textually identical to SCHEMA_TABLES in app/database.py:
# tests/test_migrations/test_migration_106.py compares the two schemas.
_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS plugin_send_guard (
    scope TEXT NOT NULL CHECK (scope IN ('instance', 'destination', 'cooldown', 'quiet')),
    subject TEXT NOT NULL,
    period_start INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, subject)
)""",
    """CREATE TABLE IF NOT EXISTS plugin_send_limits (
    name TEXT PRIMARY KEY CHECK (name IN ('instance_per_hour', 'channel_per_hour', 'contact_per_hour')),
    value INTEGER NOT NULL CHECK (value >= 1)
)""",
)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add ``plugin_send_guard`` (the Meshloom-wide hourly budget, the hourly count per
    destination, the last reply per plugin instance and sender, the last send per
    plugin instance and destination) and ``plugin_send_limits`` (the user's lowered
    limits). Both must survive a restart. Rules: ``app/plugins/quotas.py``,
    ``docs/dev/plugins.md``, "Capabilities". ``IF NOT EXISTS`` because
    ``SCHEMA_TABLES`` already creates them on a fresh database.
    """
    for statement in _STATEMENTS:
        await conn.execute(statement)
    logger.info("Plugin send guard tables ready")
    await conn.commit()
