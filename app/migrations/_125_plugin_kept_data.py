"""Migration 125: ``plugin_kept_data``, the data an uninstall kept (MSHL-366).

An uninstall with ``keep_data`` keeps the instance's ``plugin_kv`` and ``plugin_secrets``
rows, keyed by an ``instance_id`` that no longer exists. One row here records who owned
them (``plugin_id``, ``kind``, ``repository``) and when the uninstall happened, so a new
install of the same plugin from the same source takes them back
(``PluginKeptDataRepository.find`` in ``app/repository/plugins.py``) and
``PluginKeptDataRepository.prune`` deletes them after ``KEPT_DATA_RETENTION_S``.

Rows that 5.0.0 pre-releases already left orphaned (KV or secrets with no ``plugins`` row)
are recorded with an empty ``plugin_id`` and ``kind``: nothing can take them back, they are
deleted when their retention ends.
"""

import time

import aiosqlite

# Kept textually identical to SCHEMA_TABLES in app/database.py:
# tests/test_migrations/test_migration_125.py compares the two schemas.
_STATEMENT = """CREATE TABLE IF NOT EXISTS plugin_kept_data (
    instance_id TEXT PRIMARY KEY,
    plugin_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    repository TEXT,
    kept_at INTEGER NOT NULL
)"""

_ORPHANS = """
INSERT OR IGNORE INTO plugin_kept_data (instance_id, plugin_id, kind, repository, kept_at)
SELECT instance_id, '', '', NULL, ? FROM (
    SELECT instance_id FROM plugin_kv
    UNION
    SELECT instance_id FROM plugin_secrets
)
WHERE instance_id NOT IN (SELECT instance_id FROM plugins)
"""


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add the table (``IF NOT EXISTS``: ``SCHEMA_TABLES`` creates it on a fresh database)
    and record the orphans already there, so their retention starts now. A database
    without the plugin tables (partial test schemas) has no orphan to record."""
    await conn.execute(_STATEMENT)
    async with conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        " AND name IN ('plugins', 'plugin_kv', 'plugin_secrets')"
    ) as cursor:
        row = await cursor.fetchone()
    if row is not None and row[0] == 3:
        await conn.execute(_ORPHANS, (int(time.time()),))
