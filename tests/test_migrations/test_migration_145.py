"""Migration 145: deleted channels waiting for their radio slot to be emptied (MSHL-459)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_creates_the_table_and_is_idempotent():
    from app.migrations import _145_channel_slot_clears_pending as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 144)
        await conn.commit()
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 144
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        await conn.execute("INSERT INTO channel_slot_clears_pending (key) VALUES ('AB')")
        await migration.migrate(conn)  # a second run keeps the row
        async with conn.execute("SELECT COUNT(*) FROM channel_slot_clears_pending") as cursor:
            assert (await cursor.fetchone())[0] == 1
    finally:
        await conn.close()
