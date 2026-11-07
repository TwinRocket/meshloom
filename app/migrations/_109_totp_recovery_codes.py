import logging

import aiosqlite

logger = logging.getLogger(__name__)

# Kept textually identical to SCHEMA_TABLES / SCHEMA_INDEXES in app/database.py:
# tests/test_migrations/test_migration_109.py compares the two schemas.
_TABLE = """CREATE TABLE IF NOT EXISTS recovery_codes (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    code_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    used_at INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
)"""
_INDEX = "CREATE INDEX IF NOT EXISTS idx_recovery_codes_user ON recovery_codes(user_id)"


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add the TOTP second factor's storage (``app/auth/totp.py``,
    ``docs/dev/auth.md``, "Second factor"): ``recovery_codes`` (argon2id hashes,
    single use) and ``auth_settings.require_2fa_for_admins`` (off). The TOTP
    columns of ``users`` came with migration 101. ``IF NOT EXISTS`` because
    ``SCHEMA_TABLES`` already creates the table on a fresh database.
    """
    await conn.execute(_TABLE)
    await conn.execute(_INDEX)
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}
    if "auth_settings" in tables:
        col_cursor = await conn.execute("PRAGMA table_info(auth_settings)")
        if "require_2fa_for_admins" not in {row[1] for row in await col_cursor.fetchall()}:
            await conn.execute(
                "ALTER TABLE auth_settings"
                " ADD COLUMN require_2fa_for_admins INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Added auth_settings.require_2fa_for_admins")
    logger.info("TOTP recovery codes table ready")
    await conn.commit()
