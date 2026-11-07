import aiosqlite
import pytest

from app.database import Database
from app.migrations import run_migrations, set_version

_AUTH_SETTINGS = "CREATE TABLE auth_settings (id INTEGER PRIMARY KEY CHECK (id = 1))"


@pytest.mark.asyncio
async def test_upgrade_adds_an_empty_key():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 118)
        await conn.execute(_AUTH_SETTINGS)
        await conn.execute("INSERT INTO auth_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("SELECT device_cookie_key FROM auth_settings")
        assert (await cursor.fetchone())[0] is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_skips_without_auth_settings():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 118)
        await conn.commit()
        await run_migrations(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_fresh_database_has_the_column(tmp_path):
    database = Database(str(tmp_path / "fresh.db"))
    await database.connect()
    try:
        async with database.readonly() as conn:
            async with conn.execute("SELECT device_cookie_key FROM auth_settings") as cursor:
                assert (await cursor.fetchone())[0] is None
    finally:
        await database.disconnect()


@pytest.mark.asyncio
async def test_is_idempotent_and_keeps_the_key():
    from app.migrations import _119_device_cookie_key as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await conn.execute(_AUTH_SETTINGS)
        await conn.execute("INSERT INTO auth_settings (id) VALUES (1)")
        await migration.migrate(conn)
        await conn.execute("UPDATE auth_settings SET device_cookie_key = 'ab'")
        await migration.migrate(conn)
        cursor = await conn.execute("SELECT device_cookie_key FROM auth_settings")
        assert (await cursor.fetchone())[0] == "ab"
    finally:
        await conn.close()
