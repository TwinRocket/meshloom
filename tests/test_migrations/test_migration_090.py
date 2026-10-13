import aiosqlite
import pytest

from app.migrations import run_migrations, set_version

COLUMNS = (
    "enable_message_poll_fallback",
    "force_channel_slot_reconfigure",
    "load_with_autoevict",
    "legacy_env_settings_imported",
)


@pytest.mark.asyncio
async def test_adds_the_radio_workarounds_and_the_import_marker_off():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 89)
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute(f"SELECT {', '.join(COLUMNS)} FROM app_settings")
        assert tuple(await cursor.fetchone()) == (0, 0, 0, 0)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_is_idempotent_when_the_columns_exist():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 89)
        await conn.execute(
            "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
            " enable_message_poll_fallback INTEGER DEFAULT 1)"
        )
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("PRAGMA table_info(app_settings)")
        names = [row[1] for row in await cursor.fetchall()]
        for column in COLUMNS:
            assert names.count(column) == 1
        cursor = await conn.execute("SELECT enable_message_poll_fallback FROM app_settings")
        assert (await cursor.fetchone())[0] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_missing_app_settings_table_does_not_fail():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 89)
        await run_migrations(conn)
    finally:
        await conn.close()
