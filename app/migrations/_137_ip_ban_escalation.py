"""Migration 137: escalating ban durations for repeat offenders (MSHL-434, ``app/auth/ip_ban.py``).

- ``auth_settings.ip_ban_max_duration_minutes`` (1440): the longest a repeated
  ban may last. Added only when the table exists (``SCHEMA_TABLES`` creates it
  on a fresh database before the migrations run).
- ``ip_bans.strikes`` (1): how many bans in a row the key has had. Existing
  rows count as a first ban. Added only when the table exists (migration 135
  creates it).

Idempotent: a second run changes nothing.
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def _columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cursor.fetchall()}


async def migrate(conn: aiosqlite.Connection) -> None:
    settings = await _columns(conn, "auth_settings")
    if settings and "ip_ban_max_duration_minutes" not in settings:
        await conn.execute(
            "ALTER TABLE auth_settings ADD COLUMN"
            " ip_ban_max_duration_minutes INTEGER NOT NULL DEFAULT 1440"
        )
        logger.info("Added auth_settings.ip_ban_max_duration_minutes")
    bans = await _columns(conn, "ip_bans")
    if bans and "strikes" not in bans:
        await conn.execute("ALTER TABLE ip_bans ADD COLUMN strikes INTEGER NOT NULL DEFAULT 1")
        logger.info("Added ip_bans.strikes")
    await conn.commit()
