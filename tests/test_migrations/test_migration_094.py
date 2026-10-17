import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


@pytest.mark.asyncio
async def test_adds_auto_clock_wraparound_on_for_an_existing_row():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 93)
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("SELECT auto_clock_wraparound FROM app_settings")
        assert (await cursor.fetchone())[0] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_is_idempotent_and_keeps_an_existing_value():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 93)
        await conn.execute(
            "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
            " auto_clock_wraparound INTEGER DEFAULT 1)"
        )
        await conn.execute("INSERT INTO app_settings (id, auto_clock_wraparound) VALUES (1, 0)")
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("PRAGMA table_info(app_settings)")
        names = [row[1] for row in await cursor.fetchall()]
        assert names.count("auto_clock_wraparound") == 1
        cursor = await conn.execute("SELECT auto_clock_wraparound FROM app_settings")
        assert (await cursor.fetchone())[0] == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_missing_app_settings_table_does_not_fail():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 93)
        await run_migrations(conn)
    finally:
        await conn.close()
