"""Migration 135: the built-in per-address ban (MSHL-422, ``app/auth/ip_ban.py``).

- ``auth_settings``: ``ip_ban_enabled`` (on), ``ip_ban_max_failures`` (10),
  ``ip_ban_window_minutes`` (10), ``ip_ban_duration_minutes`` (60). Added only
  when the table exists (``SCHEMA_TABLES`` creates it on a fresh database
  before the migrations run).
- ``ip_bans``: one row per banned address key (an IPv4 address or an IPv6
  /64), until ``expires_at`` (epoch seconds).
- ``ip_ban_whitelist``: the addresses and networks never banned, managed with
  ``meshloom-admin whitelist`` or the Access settings.

Idempotent: a second run changes nothing.
"""

import logging

import aiosqlite

logger = logging.getLogger(__name__)

_COLUMNS = (
    ("ip_ban_enabled", "INTEGER NOT NULL DEFAULT 1"),
    ("ip_ban_max_failures", "INTEGER NOT NULL DEFAULT 10"),
    ("ip_ban_window_minutes", "INTEGER NOT NULL DEFAULT 10"),
    ("ip_ban_duration_minutes", "INTEGER NOT NULL DEFAULT 60"),
)

_BANS = """CREATE TABLE IF NOT EXISTS ip_bans (
    address TEXT PRIMARY KEY,
    banned_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    failures INTEGER NOT NULL,
    route TEXT NOT NULL
)"""
_BANS_INDEX = "CREATE INDEX IF NOT EXISTS idx_ip_bans_expires ON ip_bans(expires_at)"
_WHITELIST = """CREATE TABLE IF NOT EXISTS ip_ban_whitelist (
    network TEXT PRIMARY KEY,
    note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
)"""


async def migrate(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(auth_settings)")
    columns = {row[1] for row in await cursor.fetchall()}
    if columns:
        for name, definition in _COLUMNS:
            if name not in columns:
                await conn.execute(f"ALTER TABLE auth_settings ADD COLUMN {name} {definition}")
                logger.info("Added auth_settings.%s", name)
    await conn.execute(_BANS)
    await conn.execute(_BANS_INDEX)
    await conn.execute(_WHITELIST)
    await conn.commit()
