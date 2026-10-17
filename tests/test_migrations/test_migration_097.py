import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


@pytest.mark.asyncio
async def test_adds_push_allow_private_endpoints_off_for_an_existing_row():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 96)
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("SELECT push_allow_private_endpoints FROM app_settings")
        assert (await cursor.fetchone())[0] == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_is_idempotent_and_keeps_an_existing_value():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 96)
        await conn.execute(
            "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
            " push_allow_private_endpoints INTEGER DEFAULT 0)"
        )
        await conn.execute(
            "INSERT INTO app_settings (id, push_allow_private_endpoints) VALUES (1, 1)"
        )
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("PRAGMA table_info(app_settings)")
        names = [row[1] for row in await cursor.fetchall()]
        assert names.count("push_allow_private_endpoints") == 1
        cursor = await conn.execute("SELECT push_allow_private_endpoints FROM app_settings")
        assert (await cursor.fetchone())[0] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_missing_app_settings_table_does_not_fail():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 96)
        await run_migrations(conn)
    finally:
        await conn.close()
