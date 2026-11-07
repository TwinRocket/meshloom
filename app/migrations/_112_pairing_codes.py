import logging

import aiosqlite

logger = logging.getLogger(__name__)

# Kept textually identical to SCHEMA_TABLES / SCHEMA_INDEXES in app/database.py:
# tests/test_migrations/test_migration_112.py compares the two schemas.
_TABLE = """CREATE TABLE IF NOT EXISTS pairing_codes (
    id TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL UNIQUE,
    created_by INTEGER NOT NULL,
    label TEXT NOT NULL,
    scopes TEXT NOT NULL,
    urls TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER,
    revoked_at INTEGER,
    token_id TEXT,
    device_name TEXT,
    extensions TEXT,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (token_id) REFERENCES api_tokens(id) ON DELETE SET NULL
)"""
_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_pairing_codes_user ON pairing_codes(created_by, created_at)"
)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add ``pairing_codes``: the single-use codes a device redeems for a device
    token (``app/auth/pairing.py``, ``docs/dev/device-pairing.md``). Only the
    SHA-256 of a code is stored. ``IF NOT EXISTS`` because ``SCHEMA_TABLES``
    already creates the table on a fresh database.
    """
    await conn.execute(_TABLE)
    await conn.execute(_INDEX)
    logger.info("Pairing codes table ready")
    await conn.commit()
