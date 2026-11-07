"""Migration 134: ``auth_settings.addresses_identify_clients`` (MSHL-424)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_adds_the_setting_off_and_is_idempotent():
    from app.migrations import _134_addresses_identify_clients as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 133)
        await conn.execute("CREATE TABLE auth_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO auth_settings (id) VALUES (1)")
        await conn.commit()
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 133
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        async with conn.execute("SELECT addresses_identify_clients FROM auth_settings") as cursor:
            assert (await cursor.fetchone())[0] == 0
        await migration.migrate(conn)  # a second run changes nothing
        async with conn.execute("PRAGMA table_info(auth_settings)") as cursor:
            names = [row[1] for row in await cursor.fetchall()]
        assert names.count("addresses_identify_clients") == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_without_the_table_does_nothing():
    from app.migrations import _134_addresses_identify_clients as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await migration.migrate(conn)
        async with conn.execute("SELECT name FROM sqlite_master") as cursor:
            assert await cursor.fetchall() == []
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_fresh_database_has_the_setting_off(tmp_path):
    from app.database import Database

    database = Database(str(tmp_path / "fresh.db"))
    await database.connect()
    try:
        async with database.readonly() as conn:
            assert await get_version(conn) == LATEST_SCHEMA_VERSION
            async with conn.execute("SELECT addresses_identify_clients FROM auth_settings") as cur:
                assert (await cur.fetchone())[0] == 0
    finally:
        await database.disconnect()
