"""Migration 136: HTTP Basic auth removed, "login-free on the local network" (MSHL-100).

- ``auth_settings.lan_login_free`` (off): the master switch of the trusted
  network rules (``app/auth/providers/trusted_network.py``). It is added off on
  every database, including one with rules: an upgraded instance whose rules
  used to sign LAN clients in now asks them to sign in until an administrator
  turns the setting on in the Access settings (an explicit, audited choice).
  A log line says so when rules exist. Added only when the table exists
  (``SCHEMA_TABLES`` creates it on a fresh database before the migrations run).
- ``auth_settings.basic_imported`` is dropped: the 4.x ``MESHCORE_BASIC_AUTH_*``
  import is gone, and the account it created stays a normal account. On a
  SQLite without ``DROP COLUMN`` (before 3.35) the unused column stays.

Idempotent: a second run changes nothing.
"""

import json
import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(auth_settings)")
    columns = {row[1] for row in await cursor.fetchall()}
    if not columns:
        await conn.commit()
        return
    if "lan_login_free" not in columns:
        await conn.execute(
            "ALTER TABLE auth_settings ADD COLUMN lan_login_free INTEGER NOT NULL DEFAULT 0"
        )
        logger.info("Added auth_settings.lan_login_free (off)")
        if "trusted_networks" in columns:
            async with conn.execute("SELECT trusted_networks FROM auth_settings") as rows:
                row = await rows.fetchone()
            try:
                rules = json.loads(row[0]) if row is not None else []
            except (TypeError, ValueError):
                rules = []
            if isinstance(rules, list) and rules:
                logger.warning(
                    "%d trusted network rule(s) no longer sign anyone in until an "
                    "administrator turns on login-free on the local network "
                    "(Settings > Access)",
                    len(rules),
                )
    if "basic_imported" in columns:
        try:
            await conn.execute("ALTER TABLE auth_settings DROP COLUMN basic_imported")
            logger.info("Dropped auth_settings.basic_imported")
        except aiosqlite.OperationalError as exc:
            if "syntax error" not in str(exc).lower() and "drop column" not in str(exc).lower():
                raise
            logger.debug("SQLite has no DROP COLUMN; basic_imported stays unused")
    await conn.commit()
