import logging

import aiosqlite

logger = logging.getLogger(__name__)

# Kept textually identical to SCHEMA_TABLES and SCHEMA_INDEXES in app/database.py:
# tests/test_migrations/test_migration_108.py compares the two schemas.
_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS api_tokens (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    scopes TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER,
    expires_at INTEGER,
    revoked_at INTEGER,
    origin TEXT NOT NULL DEFAULT 'ui' CHECK (origin IN ('ui', 'pairing')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)""",
    "CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id, created_at)",
)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add ``api_tokens``: API and device tokens (``Authorization: Bearer``), one
    row per token, its secret stored as a SHA-256 only. Rules:
    ``app/auth/providers/token.py``, ``docs/dev/auth.md``, "API and device
    tokens". ``IF NOT EXISTS`` because ``SCHEMA_TABLES`` already creates it on a
    fresh database.
    """
    for statement in _STATEMENTS:
        await conn.execute(statement)
    logger.info("API tokens table ready")
    await conn.commit()
