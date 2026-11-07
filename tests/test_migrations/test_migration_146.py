"""Migration 146: drop ``auth_settings.setup_code_hash``, the rest of the row kept."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


async def _columns(conn: aiosqlite.Connection) -> set[str]:
    async with conn.execute("PRAGMA table_info(auth_settings)") as cursor:
        return {row[1] for row in await cursor.fetchall()}


@pytest.mark.asyncio
async def test_drops_the_column_keeps_the_row_and_is_idempotent():
    from app.migrations import _146_drop_setup_code_hash as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await conn.execute(
            "CREATE TABLE auth_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
            " audit_retention_days INTEGER NOT NULL DEFAULT 366, setup_code_hash TEXT)"
        )
        await conn.execute(
            "INSERT INTO auth_settings (id, audit_retention_days, setup_code_hash)"
            " VALUES (1, 30, 'abc')"
        )
        await set_version(conn, 145)
        await conn.commit()
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 145
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        assert await _columns(conn) == {"id", "audit_retention_days"}

        await migration.migrate(conn)  # a second run changes nothing
        async with conn.execute("SELECT id, audit_retention_days FROM auth_settings") as cursor:
            assert await cursor.fetchall() == [(1, 30)]
    finally:
        await conn.close()
