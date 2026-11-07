import aiosqlite
import pytest

from app.database import Database
from app.migrations import run_migrations, set_version

_APP_SETTINGS = "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))"


@pytest.mark.asyncio
async def test_upgrade_adds_an_unpaused_state():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 119)
        await conn.execute(_APP_SETTINGS)
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute(
            "SELECT plugins_paused, plugins_paused_at, plugins_paused_by FROM app_settings"
        )
        assert tuple(await cursor.fetchone()) == (0, None, None)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_skips_without_app_settings():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 119)
        await conn.commit()
        await run_migrations(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_fresh_database_has_the_columns(tmp_path):
    database = Database(str(tmp_path / "fresh.db"))
    await database.connect()
    try:
        async with database.readonly() as conn:
            async with conn.execute("SELECT plugins_paused FROM app_settings") as cursor:
                assert (await cursor.fetchone())[0] == 0
    finally:
        await database.disconnect()


@pytest.mark.asyncio
async def test_is_idempotent_and_keeps_the_pause():
    from app.migrations import _120_plugins_pause as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await conn.execute(_APP_SETTINGS)
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await migration.migrate(conn)
        await conn.execute("UPDATE app_settings SET plugins_paused = 1, plugins_paused_by = 'a'")
        await migration.migrate(conn)
        cursor = await conn.execute("SELECT plugins_paused, plugins_paused_by FROM app_settings")
        assert tuple(await cursor.fetchone()) == (1, "a")
    finally:
        await conn.close()
