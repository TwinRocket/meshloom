import logging

import aiosqlite

logger = logging.getLogger(__name__)

# Kept textually identical to SCHEMA_TABLES / SCHEMA_INDEXES in app/database.py:
# tests/test_migrations/test_migration_110.py compares the two schemas.
_TABLE = """CREATE TABLE IF NOT EXISTS webauthn_credentials (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    credential_id BLOB NOT NULL UNIQUE,
    public_key BLOB NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    transports TEXT NOT NULL DEFAULT '[]',
    aaguid TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    backup_eligible INTEGER NOT NULL DEFAULT 0,
    backed_up INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)"""
_INDEX = "CREATE INDEX IF NOT EXISTS idx_webauthn_credentials_user ON webauthn_credentials(user_id)"


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add the passkeys' storage (``app/auth/webauthn.py``, ``docs/dev/auth.md``,
    "Passkeys"): one row per WebAuthn credential, public key and counter only.
    ``IF NOT EXISTS`` because ``SCHEMA_TABLES`` already creates the table on a
    fresh database.
    """
    await conn.execute(_TABLE)
    await conn.execute(_INDEX)
    logger.info("WebAuthn credentials table ready")
    await conn.commit()
