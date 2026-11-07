import logging

import aiosqlite

logger = logging.getLogger(__name__)

# Kept textually identical to SCHEMA_TABLES in app/database.py:
# tests/test_migrations/test_migration_105.py compares the two schemas.
_STATEMENT = """CREATE TABLE IF NOT EXISTS plugin_send_quota (
    instance_id TEXT NOT NULL,
    period TEXT NOT NULL CHECK (period IN ('hour', 'day')),
    period_start INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instance_id, period)
)"""


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add ``plugin_send_quota``: the per-instance send counters of the plugin
    ``messages.send`` capability (one fixed window per period), which must survive
    a restart. Rules: ``app/plugins/quotas.py``, ``docs/dev/plugins.md``,
    "Capabilities". ``IF NOT EXISTS`` because ``SCHEMA_TABLES`` already creates it
    on a fresh database.
    """
    await conn.execute(_STATEMENT)
    logger.info("Plugin send quota table ready")
    await conn.commit()
