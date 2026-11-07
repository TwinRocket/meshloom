import logging

import aiosqlite

logger = logging.getLogger(__name__)

# Kept textually identical to SCHEMA_TABLES / SCHEMA_INDEXES in app/database.py:
# tests/test_migrations/test_migration_103.py compares the two schemas.
_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS plugins (
    instance_id TEXT PRIMARY KEY,
    plugin_id TEXT NOT NULL,
    label TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('bundled', 'store', 'custom', 'local')),
    version TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    manifest TEXT NOT NULL DEFAULT '{}',
    config_schema TEXT NOT NULL DEFAULT '{}',
    config TEXT NOT NULL DEFAULT '{}',
    scope TEXT NOT NULL DEFAULT '{}',
    granted_permissions TEXT NOT NULL DEFAULT '{}',
    quotas TEXT NOT NULL DEFAULT '{}',
    auto_update INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    archive_sha256 TEXT,
    repository TEXT,
    installed_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'installed',
    last_error TEXT,
    attention TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
)""",
    """CREATE TABLE IF NOT EXISTS plugin_kv (
    instance_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (instance_id, key)
)""",
    """CREATE TABLE IF NOT EXISTS plugin_secrets (
    instance_id TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (instance_id, field)
)""",
    "CREATE INDEX IF NOT EXISTS idx_plugins_plugin_id ON plugins(plugin_id)",
)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add the plugin tables (``plugins``, ``plugin_kv``, ``plugin_secrets``) and
    ``app_settings.store_online`` (the Store egress consent, off).

    Additive only: the bots in ``fanout_configs`` are not touched (the switch,
    migration 122, converts them). Model and rules: ``docs/dev/plugins.md``, "Data
    model". The tables are ``IF NOT EXISTS`` because ``SCHEMA_TABLES`` already
    creates them on a fresh database.
    """
    for statement in _STATEMENTS:
        await conn.execute(statement)

    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" in {row[0] for row in await tables_cursor.fetchall()}:
        col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
        if "store_online" not in {row[1] for row in await col_cursor.fetchall()}:
            await conn.execute("ALTER TABLE app_settings ADD COLUMN store_online INTEGER DEFAULT 0")
    logger.info("Plugin tables ready (plugins, plugin_kv, plugin_secrets)")
    await conn.commit()
