"""Migration 126: ``store_repositories``, the custom GitHub repositories (MSHL-196).

One row per repository the admin added (``owner/name``, lowercase key), with the last
release it read: the tag, the manifest of ``manifest.json``, the archive's sha256 and
size, and the error of the last check. ``app/plugins/custom_repos.py`` writes it; nothing
here is fetched by the migration.
"""

import aiosqlite

# The columns of SCHEMA_TABLES in app/database.py before migration 127 added
# ``config_schema``: tests/test_migrations/test_migration_126.py compares the two.
_STATEMENT = """CREATE TABLE IF NOT EXISTS store_repositories (
    repository TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    added_at INTEGER NOT NULL,
    checked_at INTEGER,
    last_error TEXT,
    tag TEXT,
    plugin_id TEXT,
    version TEXT,
    manifest TEXT,
    archive_sha256 TEXT,
    archive_size INTEGER
)"""


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add the table (``IF NOT EXISTS``: ``SCHEMA_TABLES`` creates it on a fresh
    database)."""
    await conn.execute(_STATEMENT)
