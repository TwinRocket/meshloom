"""Migration 143: the Store is online by default (MSHL-456)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


async def _store_online(conn: aiosqlite.Connection) -> int:
    async with conn.execute("SELECT store_online FROM app_settings") as cursor:
        return (await cursor.fetchone())[0]


@pytest.mark.asyncio
async def test_existing_row_goes_online_and_a_later_off_stays_off():
    from app.migrations import _143_store_online_by_default as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await conn.execute(
            "CREATE TABLE app_settings (id INTEGER PRIMARY KEY, store_online INTEGER DEFAULT 0)"
        )
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await set_version(conn, 142)
        await conn.commit()
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 142
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        assert await _store_online(conn) == 1

        await migration.migrate(conn)  # a second run changes nothing
        assert await _store_online(conn) == 1
    finally:
        await conn.close()
