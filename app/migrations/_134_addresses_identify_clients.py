"""Migration 134: ``auth_settings.addresses_identify_clients`` (MSHL-424).

Off by default. When on, the per-address login rules (``app/auth/ratelimit.py``,
"Shared addresses") apply on a Docker install without a declared trusted proxy:
the admin states that the TCP peer is the client, not the Docker gateway.
Idempotent.
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(auth_settings)")
    columns = {row[1] for row in await cursor.fetchall()}
    if columns and "addresses_identify_clients" not in columns:
        await conn.execute(
            "ALTER TABLE auth_settings"
            " ADD COLUMN addresses_identify_clients INTEGER NOT NULL DEFAULT 0"
        )
        logger.info("Added auth_settings.addresses_identify_clients")
    await conn.commit()
