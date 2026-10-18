import aiosqlite
import pytest

from app.database import Database
from app.migrations import run_migrations, set_version

_ROW = "SELECT share_enabled, share_shorten_mode, share_preview_policy, share_instance_seed"


@pytest.mark.asyncio
async def test_upgrade_gives_an_existing_row_off_auto_private_and_no_seed():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 101)
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute(f"{_ROW} FROM app_settings")
        assert tuple(await cursor.fetchone()) == (0, "auto", "private", "")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_fresh_database_has_the_same_defaults(tmp_path):
    database = Database(str(tmp_path / "fresh.db"))
    await database.connect()
    try:
        async with database.readonly() as conn:
            async with conn.execute(f"{_ROW} FROM app_settings WHERE id = 1") as cursor:
                assert tuple(await cursor.fetchone()) == (0, "auto", "private", "")
    finally:
        await database.disconnect()


@pytest.mark.asyncio
async def test_is_idempotent_and_keeps_existing_values():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 101)
        await conn.execute(
            "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
            " share_enabled INTEGER DEFAULT 0, share_shorten_mode TEXT DEFAULT 'auto')"
        )
        await conn.execute(
            "INSERT INTO app_settings (id, share_enabled, share_shorten_mode) VALUES (1, 1, 'never')"
        )
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("PRAGMA table_info(app_settings)")
        names = [row[1] for row in await cursor.fetchall()]
        assert names.count("share_enabled") == 1
        assert names.count("share_shorten_mode") == 1
        cursor = await conn.execute(f"{_ROW} FROM app_settings")
        assert tuple(await cursor.fetchone()) == (1, "never", "private", "")
    finally:
        await conn.close()
