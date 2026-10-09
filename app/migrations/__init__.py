"""
Database migrations using SQLite's user_version pragma.

Migrations run automatically on startup. The user_version pragma tracks
which migrations have been applied (defaults to 0 for existing databases).

Each migration lives in its own file: ``_NNN_description.py``, exposing an
``async def migrate(conn)`` entry point.  The runner auto-discovers files by
numeric prefix and executes them in order.

This approach is safe for existing users - their databases have user_version=0,
so all migrations run in order on first startup after upgrade.

Each migration runs inside one explicit transaction together with the
``user_version`` bump, so a crash or exception mid-migration leaves the
database exactly as it was before that migration (SQLite DDL and the
``user_version`` pragma are both transactional). ``conn.commit()`` calls made
by a migration body are deferred to the runner. A migration that cannot run
in a transaction (``VACUUM``, ``journal_mode`` changes) sets the module-level
flag ``TRANSACTIONAL = False`` and is responsible for its own idempotence.
"""

import importlib
import logging
import pkgutil
import re
from typing import Any, cast

import aiosqlite

logger = logging.getLogger(__name__)


async def get_version(conn: aiosqlite.Connection) -> int:
    """Get current schema version from SQLite user_version pragma."""
    cursor = await conn.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    return row[0] if row else 0


async def set_version(conn: aiosqlite.Connection, version: int) -> None:
    """Set schema version using SQLite user_version pragma."""
    await conn.execute(f"PRAGMA user_version = {version}")


class _DeferredCommitConnection:
    """Connection proxy whose ``commit()`` is a no-op.

    Migration bodies historically call ``await conn.commit()`` themselves;
    deferring those lets the runner commit the migration and the version bump
    atomically.
    """

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def commit(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


async def _apply_transactional(conn: aiosqlite.Connection, mod: Any, num: int) -> None:
    if conn.in_transaction:
        await conn.commit()
    await conn.execute("BEGIN")
    try:
        await mod.migrate(cast(aiosqlite.Connection, _DeferredCommitConnection(conn)))
        await set_version(conn, num)
        await conn.commit()
    except BaseException:
        await conn.rollback()
        raise


async def run_migrations(conn: aiosqlite.Connection) -> int:
    """
    Run all pending migrations.

    Returns the number of migrations applied.
    """
    version = await get_version(conn)
    applied = 0

    for module_info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        match = re.match(r"_(\d+)_", module_info.name)
        if not match:
            continue
        num = int(match.group(1))
        if num <= version:
            continue
        logger.info("Applying migration %d: %s", num, module_info.name)
        mod = importlib.import_module(f"{__name__}.{module_info.name}")
        if getattr(mod, "TRANSACTIONAL", True):
            await _apply_transactional(conn, mod, num)
        else:
            await mod.migrate(conn)
            await set_version(conn, num)
            await conn.commit()
        version = num
        applied += 1

    if applied > 0:
        logger.info(
            "Applied %d migration(s), schema now at version %d", applied, await get_version(conn)
        )
    else:
        logger.debug("Schema up to date at version %d", version)

    return applied
