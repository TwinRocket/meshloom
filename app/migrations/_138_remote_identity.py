"""Migration 138: Meshloom Remote identity, registration and devices (MSHL-79).

- ``app_settings.remote_instance_seed`` (``''``): the Remote instance identity seed
  (``app/services/instance_identity.py``), distinct from ``share_instance_seed``.
  Added only when ``app_settings`` exists.
- ``remote_state``: one row (``id = 1``) with the opt-in (``enabled``, off), the
  registration (``slug``, ``hostname``, ``relay_url``, ``registered_at``), the pass
  generation (``gate_gen``) and the last registry error code.
- ``remote_devices``: the devices that hold a pass (``app/services/remote.py``).
- ``remote_enroll_tokens``: the enrolment tokens issued for a QR code, by ``jti``.

Every new table is ``IF NOT EXISTS``; no ``SCHEMA_TABLES`` change. Idempotent.
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)

_TABLES = (
    """CREATE TABLE IF NOT EXISTS remote_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    slug TEXT NOT NULL DEFAULT '',
    hostname TEXT NOT NULL DEFAULT '',
    relay_url TEXT NOT NULL DEFAULT '',
    registered_at INTEGER,
    gate_gen INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
)""",
    """CREATE TABLE IF NOT EXISTS remote_devices (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    label TEXT,
    origin TEXT NOT NULL,
    gen INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    pass_expires_at INTEGER NOT NULL,
    last_seen_at INTEGER,
    revoked_at INTEGER
)""",
    "CREATE INDEX IF NOT EXISTS idx_remote_devices_user ON remote_devices(user_id)",
    """CREATE TABLE IF NOT EXISTS remote_enroll_tokens (
    jti TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    gen INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER
)""",
)


async def migrate(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await cursor.fetchall()}
    if columns and "remote_instance_seed" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN remote_instance_seed TEXT DEFAULT ''"
        )
        logger.info("Added app_settings.remote_instance_seed")
    for statement in _TABLES:
        await conn.execute(statement)
    await conn.execute("INSERT OR IGNORE INTO remote_state (id) VALUES (1)")
    await conn.commit()
