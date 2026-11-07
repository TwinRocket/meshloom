import logging

import aiosqlite

logger = logging.getLogger(__name__)

# Kept textually identical to SCHEMA_TABLES / SCHEMA_INDEXES in app/database.py:
# tests/test_migrations/test_migration_101.py compares the two schemas.
_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    password_hash TEXT,
    role TEXT NOT NULL CHECK (role IN ('admin', 'member', 'viewer', 'kiosk')),
    provider TEXT NOT NULL DEFAULT 'local' CHECK (provider IN ('local', 'ingress', 'system')),
    external_id TEXT,
    disabled INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    totp_secret TEXT,
    totp_enabled_at INTEGER,
    totp_last_counter INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(provider, external_id)
)""",
    """CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    ip TEXT,
    user_agent TEXT,
    via TEXT,
    revoked_at INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)""",
    """CREATE TABLE IF NOT EXISTS auth_audit (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    actor_user_id INTEGER,
    actor_token_id TEXT,
    action TEXT NOT NULL,
    target TEXT,
    ip TEXT,
    via TEXT,
    outcome TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}'
)""",
    """CREATE TABLE IF NOT EXISTS auth_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    trusted_networks TEXT NOT NULL DEFAULT '[]',
    trusted_proxies TEXT NOT NULL DEFAULT '[]',
    public_urls TEXT NOT NULL DEFAULT '[]',
    host_guard_mode TEXT NOT NULL DEFAULT 'observe'
        CHECK (host_guard_mode IN ('observe', 'enforce')),
    ingress_default_role TEXT NOT NULL DEFAULT 'member'
        CHECK (ingress_default_role IN ('admin', 'member', 'viewer', 'kiosk')),
    session_lifetime_days INTEGER NOT NULL DEFAULT 30,
    audit_retention_days INTEGER NOT NULL DEFAULT 366,
    basic_imported INTEGER NOT NULL DEFAULT 0,
    setup_code_hash TEXT
)""",
    "INSERT OR IGNORE INTO auth_settings (id) VALUES (1)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_auth_audit_ts ON auth_audit(ts)",
)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add the local accounts data model: ``users``, ``sessions``, ``auth_audit``
    and the single-row ``auth_settings``.

    Nothing reads them yet (no login, no middleware): the auth core (Access
    ticket T2) builds on them. Schema and repository: ``docs/dev/auth.md``. Every statement
    is ``IF NOT EXISTS`` / ``OR IGNORE``, because ``SCHEMA_TABLES`` already
    creates the tables before migrations run.
    """
    for statement in _STATEMENTS:
        await conn.execute(statement)
    logger.info("Auth data model ready (users, sessions, auth_audit, auth_settings)")
    await conn.commit()
